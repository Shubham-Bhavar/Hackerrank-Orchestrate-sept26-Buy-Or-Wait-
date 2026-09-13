#!/usr/bin/env python3
"""
sweep.py - score configurations on TOTAL OUTPUT CELLS, not just one column.

calibrate.py optimises `amount_safe_to_pay` alone. But six of the seven scored
columns are exact-match, and the downstream columns (status, method, plan,
spending changes) turn out to be far more forgiving than the safe amount: a
forecast can be numerically off and still land the right decision. So the
objective that actually matches the scoring is total cells correct.

    python3 sweep.py              # sweep the main knobs
    python3 sweep.py --single     # score the current DEFAULT only
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from config import DEFAULT                                       # noqa: E402
from evidence import (                                           # noqa: E402
    build_evidence,
    load_verified_image_amounts,
    resolve_income_conflicts,
)
from solve import solve                                          # noqa: E402
from state import load_dataset                                   # noqa: E402
from validate import row_from_decision                           # noqa: E402

SCORED = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]


def score(ds, cfg, verified) -> tuple[dict[str, int], list[tuple[str, str, str, str]]]:
    per = {c: 0 for c in SCORED}
    misses: list[tuple[str, str, str, str]] = []
    for req in ds.sample_requests:
        label = ds.sample_labels[req.request_id]
        try:
            ev = resolve_income_conflicts(
                build_evidence(ds, req, None, verified, ds.messages_by_user))
            row = row_from_decision(solve(ds, req, cfg, ev), "")
        except Exception:                                        # noqa: BLE001
            continue
        for col in SCORED:
            got, want = str(row.get(col, "")).strip(), str(label[col]).strip()
            if col == "amount_safe_to_pay":
                try:
                    ok = abs(float(got or 0) - float(want or 0)) < 0.01
                except ValueError:
                    ok = got == want
            else:
                ok = got == want
            if ok:
                per[col] += 1
            else:
                misses.append((req.request_id, col, got, want))
    return per, misses


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(HERE, "..", "dataset"))
    ap.add_argument("--single", action="store_true")
    args = ap.parse_args()

    ds = load_dataset(args.dataset)
    verified = load_verified_image_amounts(
        os.path.join(HERE, "verified_image_amounts.json"))

    if args.single:
        per, misses = score(ds, DEFAULT, verified)
        total = sum(per.values())
        for c in SCORED:
            print(f"  {c:<34}{per[c]}/25")
        print(f"  {'TOTAL CELLS':<34}{total}/150")
        print("\nmisses:")
        for rid, col, got, want in misses:
            print(f"  {rid:<13}{col:<32}got={got[:34]!r:<36}want={want[:34]!r}")
        return

    grid = {
        "recurrence_min_occurrences": [2, 3],
        "recurrence_lookback_days": [95, 110, 125, 140, 150, 165, 185],
        "recurrence_amount_mode": ["mean", "median"],
        "income_amount_mode": ["median", "mean"],
        "horizon_inclusive": [True, False],
        "include_flows_on_request_date": [True, False],
    }
    keys = list(grid)
    results = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        cfg = DEFAULT.with_(**dict(zip(keys, combo)))
        per, _ = score(ds, cfg, verified)
        results.append((sum(per.values()), per, dict(zip(keys, combo))))
    results.sort(key=lambda r: -r[0])

    print(f"{len(results)} configurations\n")
    print(f"{'total':>6}  {'safe':>5}{'stat':>5}{'meth':>5}{'plan':>5}{'earl':>5}"
          f"{'chng':>5}   configuration")
    for total, per, cfgd in results[:15]:
        print(f"{total:>6}  {per['amount_safe_to_pay']:>5}"
              f"{per['affordability_status']:>5}"
              f"{per['recommended_payment_method']:>5}"
              f"{per['payment_plan']:>5}"
              f"{per['earliest_date_for_full_payment']:>5}"
              f"{per['spending_changes_needed']:>5}   "
              + ", ".join(f"{k.split('_')[-1]}={v}" for k, v in cfgd.items()))


if __name__ == "__main__":
    main()
