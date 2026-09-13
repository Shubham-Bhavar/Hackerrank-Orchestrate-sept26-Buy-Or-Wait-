#!/usr/bin/env python3
"""
verify_submission.py - independent audit of the finished output.csv.

Deliberately does NOT import validate.py. That module is part of the pipeline, so
using it here would only prove the pipeline agrees with itself. This script is
written from the challenge rules and the dataset, and re-derives everything it
checks. If it disagrees with validate.py, one of them has a bug and that is worth
knowing before submitting.

    cd code && python3 verify_submission.py

Exit code 0 = every check passed.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

REQUIRED_HEADER = [
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan",
    "earliest_date_for_full_payment", "spending_changes_needed",
    "decision_explanation",
]

STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later",
            "not_affordable"}
METHODS = {"full_payment", "partial_payment", "installments", "wait",
           "not_recommended"}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:\d+(\.\d{2})?$")
CHANGE_RE = re.compile(r"^(stop:[A-Za-z0-9_]+|reduce_to:[A-Za-z0-9_]+:\d+(\.\d{2})?)$")

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSES if ok else FAILURES).append(f"{name}{(' - ' + detail) if detail else ''}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    out_path = os.path.join(ROOT, "output.csv")
    ds_dir = os.path.join(ROOT, "dataset")

    print("== 1. file and header ==")
    if not os.path.exists(out_path):
        check("output.csv exists", False, out_path)
        return 1
    with open(out_path, newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    check("header matches the required column order exactly",
          header == REQUIRED_HEADER, "" if header == REQUIRED_HEADER else str(header))

    rows = read_csv(out_path)
    requests = read_csv(os.path.join(ds_dir, "requests.csv"))
    profiles = {r["user_id"]: r for r in read_csv(os.path.join(ds_dir,
                                                              "financial_profiles.csv"))}
    options = {}
    for o in read_csv(os.path.join(ds_dir, "request_payment_options.csv")):
        options.setdefault(o["request_id"], []).append(o)
    events = {}
    for e in read_csv(os.path.join(ds_dir, "financial_events.csv")):
        events[e["event_id"]] = e

    print("\n== 2. row integrity ==")
    ids = [r["request_id"] for r in rows]
    src_ids = [r["request_id"] for r in requests]
    check("exactly 250 rows", len(rows) == 250, f"got {len(rows)}")
    check("one row per request", len(rows) == len(requests))
    check("no duplicate request ids", len(set(ids)) == len(ids))
    check("no missing request ids", set(ids) == set(src_ids),
          f"missing {sorted(set(src_ids) - set(ids))[:5]}")
    check("original request order preserved", ids == src_ids)

    by_req = {r["request_id"]: r for r in requests}

    print("\n== 3. enums, dates, numbers ==")
    bad_status = [r["request_id"] for r in rows
                  if r["affordability_status"] not in STATUSES]
    check("affordability_status enum valid", not bad_status, str(bad_status[:5]))
    bad_method = [r["request_id"] for r in rows
                  if r["recommended_payment_method"] not in METHODS]
    check("recommended_payment_method enum valid", not bad_method, str(bad_method[:5]))

    bad_date = [r["request_id"] for r in rows
                if r["earliest_date_for_full_payment"]
                and not DATE_RE.match(r["earliest_date_for_full_payment"])]
    check("earliest_date is YYYY-MM-DD or blank", not bad_date, str(bad_date[:5]))

    neg, over, nonnum = [], [], []
    for r in rows:
        try:
            v = float(r["amount_safe_to_pay"])
        except ValueError:
            nonnum.append(r["request_id"])
            continue
        if v < 0:
            neg.append(r["request_id"])
        if v > float(by_req[r["request_id"]]["requested_amount"]) + 1e-6:
            over.append(r["request_id"])
    check("amount_safe_to_pay is numeric", not nonnum, str(nonnum[:5]))
    check("amount_safe_to_pay >= 0", not neg, str(neg[:5]))
    check("amount_safe_to_pay <= requested_amount", not over, str(over[:5]))

    print("\n== 4. payment plan format and arithmetic ==")
    bad_fmt, not_chrono, bad_sum, bad_none = [], [], [], []
    for r in rows:
        plan = r["payment_plan"].strip()
        rid = r["request_id"]
        status = r["affordability_status"]
        method = r["recommended_payment_method"]
        if plan == "none":
            if method != "not_recommended":
                bad_none.append(rid)
            continue
        segs = plan.split("|")
        if not all(SEG_RE.match(s) for s in segs):
            bad_fmt.append(rid)
            continue
        dates = [date.fromisoformat(s.split(":")[0]) for s in segs]
        if dates != sorted(dates):
            not_chrono.append(rid)
        total = sum(float(s.split(":")[1]) for s in segs)
        requested = float(by_req[rid]["requested_amount"])
        if method == "installments":
            # must equal a supplied option's total_payable_amount
            totals = [float(o["total_payable_amount"])
                      for o in options.get(rid, [])
                      if o["payment_method"] == "installments"]
            if not any(abs(total - t) < 0.02 for t in totals):
                bad_sum.append(f"{rid}(inst {total:.2f} vs {totals})")
        else:
            if abs(total - requested) > 0.02:
                bad_sum.append(f"{rid}({total:.2f} vs {requested:.2f})")
    check("payment_plan segments match YYYY-MM-DD:amount", not bad_fmt, str(bad_fmt[:5]))
    check("payment_plan dates chronological", not not_chrono, str(not_chrono[:5]))
    check("payment_plan totals correct (requested, or option total for installments)",
          not bad_sum, str(bad_sum[:5]))
    check("plan 'none' only when not_recommended", not bad_none, str(bad_none[:5]))

    print("\n== 5. status / method / earliest-date consistency ==")
    allowed = {"affordable_now": {"full_payment"},
               "affordable_later": {"wait"},
               "not_affordable": {"not_recommended"},
               "affordable_with_plan": {"full_payment", "partial_payment",
                                        "installments"}}
    mismatch = [r["request_id"] for r in rows
                if r["recommended_payment_method"]
                not in allowed[r["affordability_status"]]]
    check("status/method mapping holds", not mismatch, str(mismatch[:5]))

    bad_now = [r["request_id"] for r in rows
               if r["affordability_status"] == "affordable_now"
               and r["earliest_date_for_full_payment"]
               != by_req[r["request_id"]]["request_date"]]
    check("affordable_now implies earliest == request_date", not bad_now,
          str(bad_now[:5]))

    bad_na = [r["request_id"] for r in rows
              if r["affordability_status"] == "not_affordable"
              and (r["earliest_date_for_full_payment"] != ""
                   or r["payment_plan"] != "none")]
    check("not_affordable implies blank earliest and plan 'none'", not bad_na,
          str(bad_na[:5]))

    print("\n== 6. partial payment preconditions ==")
    bad_partial = []
    for r in rows:
        if r["recommended_payment_method"] != "partial_payment":
            continue
        rid = r["request_id"]
        req = by_req[rid]
        segs = r["payment_plan"].split("|")
        safe = float(r["amount_safe_to_pay"])
        requested = float(req["requested_amount"])
        reasons = []
        if len(segs) != 2:
            reasons.append("not exactly 2 payments")
        if r["affordability_status"] != "affordable_with_plan":
            reasons.append("status not affordable_with_plan")
        if req["allows_partial_payment"].lower() != "true":
            reasons.append("request does not allow partial")
        if not (0 < safe < requested):
            reasons.append("safe not strictly between 0 and requested")
        methods = profiles[req["user_id"]]["payment_methods_user_will_consider"]
        if "partial_payment" not in methods.split("|"):
            reasons.append("user does not accept partial_payment")
        e = r["earliest_date_for_full_payment"]
        if not e or e > req["desired_completion_date"]:
            reasons.append("earliest after desired_completion_date")
        if abs(sum(float(s.split(":")[1]) for s in segs) - requested) > 0.02:
            reasons.append("payments do not sum to requested")
        if reasons:
            bad_partial.append(f"{rid}: {'; '.join(reasons)}")
    n_partial = sum(1 for r in rows
                    if r["recommended_payment_method"] == "partial_payment")
    check(f"all {n_partial} partial_payment rows satisfy every precondition",
          not bad_partial, str(bad_partial[:3]))

    print("\n== 7. installment plans match a supplied payment_option_id ==")
    unmatched = []
    for r in rows:
        if r["recommended_payment_method"] != "installments":
            continue
        rid = r["request_id"]
        segs = r["payment_plan"].split("|")
        n, first = len(segs), segs[0].split(":")[0]
        amt = float(segs[0].split(":")[1])
        ok = False
        for o in options.get(rid, []):
            if (o["payment_method"] == "installments"
                    and int(o["number_of_payments"]) == n
                    and o["first_payment_date"] == first
                    and abs(float(o["payment_amount"]) - amt) < 0.02):
                ok = True
                break
        if not ok:
            unmatched.append(rid)
    n_inst = sum(1 for r in rows
                 if r["recommended_payment_method"] == "installments")
    check(f"all {n_inst} installment plans copy a real supplied option",
          not unmatched, str(unmatched[:5]))

    print("\n== 8. spending changes ==")
    bad_change, bad_reduce, both = [], [], []
    for r in rows:
        v = r["spending_changes_needed"].strip()
        rid = r["request_id"]
        if v == "none":
            continue
        parts = v.split("|")
        if len(parts) > 3:
            bad_change.append(f"{rid}: more than 3 changes")
        seen: dict[str, set] = {}
        for p in parts:
            if not CHANGE_RE.match(p):
                bad_change.append(f"{rid}: malformed {p}")
                continue
            bits = p.split(":")
            action, eid = bits[0], bits[1]
            seen.setdefault(eid, set()).add(action)
            ev = events.get(eid)
            if ev is None:
                bad_change.append(f"{rid}: unknown event {eid}")
                continue
            if action == "stop" and ev["flexibility"] not in (
                    "stoppable", "reducible_or_stoppable"):
                bad_change.append(f"{rid}: stop on non-stoppable {eid}")
            if action == "reduce_to":
                if ev["flexibility"] not in ("reducible", "reducible_or_stoppable"):
                    bad_change.append(f"{rid}: reduce on non-reducible {eid}")
                target = float(bits[2])
                mam = ev["minimum_allowed_amount"].strip()
                if not mam or abs(float(mam) - target) > 0.005:
                    bad_reduce.append(f"{rid}: {eid} -> {target} but mam={mam!r}")
        for eid, actions in seen.items():
            if len(actions) > 1:
                both.append(f"{rid}:{eid}")
    n_changes = sum(1 for r in rows if r["spending_changes_needed"] != "none")
    check(f"all {n_changes} rows with changes are well-formed and flexible",
          not bad_change, str(bad_change[:3]))
    check("every reduce_to equals the event's minimum_allowed_amount",
          not bad_reduce, str(bad_reduce[:3]))
    check("stop and reduce_to never applied to the same event", not both,
          str(both[:3]))

    print("\n== 9. safety: minimum balance never violated ==")
    from config import DEFAULT
    from evidence import (build_evidence, load_verified_image_amounts,
                          resolve_income_conflicts)
    from state import build_state, load_dataset
    ds = load_dataset(ds_dir)
    verified = load_verified_image_amounts(
        os.path.join(HERE, "verified_image_amounts.json"))
    breaches = []
    for req in ds.requests:
        row = next(r for r in rows if r["request_id"] == req.request_id)
        safe = float(row["amount_safe_to_pay"])
        if safe == 0.0:
            continue
        ev = resolve_income_conflicts(
            build_evidence(ds, req, None, verified, ds.messages_by_user))
        st = build_state(ds, req, DEFAULT, evidence=ev)
        for entry in st.timeline:
            if entry.closing_balance - safe < st.minimum - 1e-6:
                breaches.append(f"{req.request_id}@{entry.day}")
                break
    check("paying amount_safe_to_pay never breaches minimum_balance_to_keep "
          "on any forecast day, all 250 requests", not breaches, str(breaches[:5]))

    print("\n== 10. CSV hygiene ==")
    raw = open(out_path, encoding="utf-8").read()
    check("no embedded newline inside any explanation",
          all("\n" not in r["decision_explanation"] for r in rows))
    check("file ends with a single trailing newline", raw.endswith("\n")
          and not raw.endswith("\n\n"))
    # round trip
    tmp = os.path.join("/tmp", "roundtrip.csv")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REQUIRED_HEADER,
                           quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    check("CSV round-trip is byte-identical",
          open(tmp, encoding="utf-8").read() == raw)

    print("\n== summary ==")
    print(f"  {len(PASSES)} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(f"    FAILED: {f}")
        return 1
    print("  OUTPUT VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
