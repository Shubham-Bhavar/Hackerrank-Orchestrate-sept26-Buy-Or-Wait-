#!/usr/bin/env python3
"""
analyse.py - per-request error analysis over the 25 labelled samples.

Produces the table behind FINAL_ERROR_ANALYSIS.md: for every sample, the
predicted vs expected values, the reconstructed forecast that produced them,
and the evidence that fed the reconstruction.

    python3 analyse.py            # table + grouped causes
    python3 analyse.py --dump R   # full flow dump for one request
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from config import DEFAULT                                       # noqa: E402
from evidence import (                                           # noqa: E402
    build_evidence,
    load_verified_image_amounts,
    resolve_income_conflicts,
)
from solve import solve                                          # noqa: E402
from state import build_state, load_dataset                      # noqa: E402
from validate import row_from_decision                           # noqa: E402


def analyse(ds, verified, cfg=DEFAULT):
    rows = []
    for req in ds.sample_requests:
        lab = ds.sample_labels[req.request_id]
        prof = ds.profiles[req.user_id]
        ev = resolve_income_conflicts(
            build_evidence(ds, req, None, verified, ds.messages_by_user))
        st = build_state(ds, req, cfg, evidence=ev)
        dec = solve(ds, req, cfg, ev)
        row = row_from_decision(dec, "")

        horizon_end = req.request_date + timedelta(days=cfg.horizon_days)
        inflow = sum(f.amount for f in st.flows if f.amount > 0)
        outflow = -sum(f.amount for f in st.flows if f.amount < 0)
        exp_safe = float(lab["amount_safe_to_pay"])
        got_safe = float(row["amount_safe_to_pay"])
        implied = (prof.current_available_balance
                   - prof.minimum_balance_to_keep - exp_safe)

        rows.append({
            "request_id": req.request_id,
            "user_id": req.user_id,
            "currency": prof.home_currency,
            "request_date": req.request_date,
            "requested": req.requested_amount,
            "balance": prof.current_available_balance,
            "minimum": prof.minimum_balance_to_keep,
            "exp_safe": exp_safe,
            "got_safe": got_safe,
            "abs_err": abs(got_safe - exp_safe),
            "rel_err": abs(got_safe - exp_safe) / max(abs(exp_safe), 1.0),
            "capped": abs(exp_safe - req.requested_amount) < 0.01,
            "implied_outflow": implied,
            "implied_frac": implied - round(implied),
            "exp_status": lab["affordability_status"],
            "got_status": row["affordability_status"],
            "exp_method": lab["recommended_payment_method"],
            "got_method": row["recommended_payment_method"],
            "exp_earliest": lab["earliest_date_for_full_payment"],
            "got_earliest": row["earliest_date_for_full_payment"],
            "exp_changes": lab["spending_changes_needed"],
            "got_changes": row["spending_changes_needed"],
            "proj_inflow": inflow,
            "proj_outflow": outflow,
            "trough": st.headroom(),
            "worst_day": st.worst_day(),
            "horizon_end": horizon_end,
            "n_series": len(st.series),
            "n_fixed": sum(1 for s in st.series if s.is_fixed_amount),
            "n_var": sum(1 for s in st.series if not s.is_fixed_amount),
            "cadences": sorted({int(round(s.cadence_days)) for s in st.series}),
            "images": len(ev.images),
            "msgs": len(ds.messages_by_user.get(req.user_id, [])),
            "income_notes": [n for n in st.evidence_notes if "income" in n.lower()
                             or "salary" in n.lower()][:2],
            "unresolved": list(ev.unresolved),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(HERE, "..", "dataset"))
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    ds = load_dataset(args.dataset)
    verified = load_verified_image_amounts(
        os.path.join(HERE, "verified_image_amounts.json"))

    if args.dump:
        req = next(r for r in ds.sample_requests if r.request_id == args.dump)
        ev = resolve_income_conflicts(
            build_evidence(ds, req, None, verified, ds.messages_by_user))
        st = build_state(ds, req, DEFAULT, evidence=ev)
        print(f"{req.request_id} {req.user_id} {req.request_date} "
              f"requested={req.requested_amount}")
        print(f"expected safe = {ds.sample_labels[req.request_id]['amount_safe_to_pay']}")
        print(f"balance={st.opening_balance} minimum={st.minimum} "
              f"trough={st.headroom():.2f} worst={st.worst_day()}")
        print("\nseries:")
        for s in sorted(st.series, key=lambda s: s.description):
            print(f"  {s.direction:<7}{'FIXED' if s.is_fixed_amount else 'var  '} "
                  f"n={len(s.occurrences):<3} cad={s.cadence_days:>5.0f} "
                  f"amt={s.projected_amount:>12,.2f}  {s.description}")
        print("\nflows:")
        for f in sorted(st.flows, key=lambda f: f.when):
            print(f"  {f.when} {f.amount:>14,.2f} [{f.source}] {f.label}")
        print("\nevidence notes:")
        for n in st.evidence_notes:
            print("  " + n)
        return

    rows = analyse(ds, verified)

    print("== per-request table ==")
    hdr = (f"{'request':<12}{'cur':<5}{'requested':>16}{'exp_safe':>16}"
           f"{'got_safe':>16}{'rel_err':>9}  {'status exp/got':<44}"
           f"{'method exp/got':<34}")
    print(hdr)
    for r in rows:
        print(f"{r['request_id']:<12}{r['currency']:<5}{r['requested']:>16,.2f}"
              f"{r['exp_safe']:>16,.2f}{r['got_safe']:>16,.2f}{r['rel_err']:>9.3f}  "
              f"{r['exp_status'] + '/' + r['got_status']:<44}"
              f"{r['exp_method'] + '/' + r['got_method']:<34}")

    print("\n== forecast internals ==")
    print(f"{'request':<12}{'balance':>16}{'minimum':>14}{'inflow':>16}"
          f"{'outflow':>16}{'trough':>16}{'worst':<12}{'series f/v':<12}{'cadences'}")
    for r in rows:
        print(f"{r['request_id']:<12}{r['balance']:>16,.0f}{r['minimum']:>14,.0f}"
              f"{r['proj_inflow']:>16,.0f}{r['proj_outflow']:>16,.0f}"
              f"{r['trough']:>16,.0f}{str(r['worst_day']):<12}"
              f"{str(r['n_fixed']) + '/' + str(r['n_var']):<12}{r['cadences']}")

    print("\n== implied outflow fingerprint (label arithmetic, config-free) ==")
    whole = tot = 0
    for r in rows:
        if r["capped"]:
            continue
        tot += 1
        whole += abs(r["implied_frac"]) < 1e-6
        print(f"{r['request_id']:<12}{r['implied_outflow']:>18,.2f}"
              f"{r['implied_frac']:>9.2f}")
    print(f"whole-number implied outflow: {whole}/{tot}")

    print("\n== grouped failure causes ==")
    groups: dict[str, list[str]] = {}

    def add(cause, rid):
        groups.setdefault(cause, []).append(rid)

    for r in rows:
        if r["capped"] and r["got_safe"] == r["exp_safe"]:
            add("A. capped and correct (clamp hides the forecast)", r["request_id"])
        if r["exp_status"] != r["got_status"]:
            if r["got_status"] == "affordable_now" and r["exp_changes"] != "none":
                add("B. forecast too optimistic: we see no need for a spending change",
                    r["request_id"])
            elif r["got_status"] == "not_affordable":
                add("C. forecast too pessimistic: we declare not_affordable",
                    r["request_id"])
            else:
                add("D. other status mismatch", r["request_id"])
        if (r["exp_earliest"] != r["got_earliest"]
                and r["exp_status"] == r["got_status"]):
            add("E. earliest date off while status is right", r["request_id"])
        if not r["capped"] and r["rel_err"] > 0.30:
            add("F. safe amount off by more than 30%", r["request_id"])
        elif not r["capped"] and 0.0 < r["rel_err"] <= 0.30:
            add("G. safe amount off by 30% or less (right shape, wrong precision)",
                r["request_id"])
    for cause in sorted(groups):
        print(f"  {cause}: {len(groups[cause])}")
        print(f"      {', '.join(groups[cause])}")

    over = [r for r in rows if not r["capped"] and r["got_safe"] > r["exp_safe"] + 0.01]
    under = [r for r in rows if not r["capped"] and r["got_safe"] < r["exp_safe"] - 0.01]
    print(f"\n  over-predicting headroom:  {len(over)}  "
          f"({', '.join(r['request_id'] for r in over)})")
    print(f"  under-predicting headroom: {len(under)}  "
          f"({', '.join(r['request_id'] for r in under)})")


if __name__ == "__main__":
    main()
