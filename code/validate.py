"""
validate.py - the last gate before output.csv.

Every rule the problem statement states about the output is checked here. A row
that fails is REPAIRED DETERMINISTICALLY, never sent back to a model. If a row
cannot be repaired it is replaced with the conservative fallback, which is
always valid.

The validator is also runnable standalone against a finished output.csv, so the
file can be checked without re-running the pipeline.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solve import Decision, fmt_plan, fmt_safe          # noqa: E402
from state import Dataset, Request, load_dataset        # noqa: E402

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

STATUSES = {"affordable_now", "affordable_with_plan",
            "affordable_later", "not_affordable"}
METHODS = {"full_payment", "partial_payment", "installments",
           "wait", "not_recommended"}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:\d+(\.\d{2})?$")
CHANGE_RE = re.compile(r"^(stop:[A-Za-z0-9_]+|reduce_to:[A-Za-z0-9_]+:\d+(\.\d{2})?)$")


@dataclass
class Issue:
    request_id: str
    rule: str
    detail: str


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)

    def add(self, rid: str, rule: str, detail: str) -> None:
        self.issues.append(Issue(rid, rule, detail))

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        if self.ok:
            return "all rows valid"
        by_rule: dict[str, int] = {}
        for i in self.issues:
            by_rule[i.rule] = by_rule.get(i.rule, 0) + 1
        return "; ".join(f"{k}: {v}" for k, v in sorted(by_rule.items()))


def row_from_decision(dec: Decision, explanation: str) -> dict:
    return {
        "request_id": dec.request_id,
        "amount_safe_to_pay": fmt_safe(dec.amount_safe_to_pay),
        "affordability_status": dec.affordability_status,
        "recommended_payment_method": dec.recommended_payment_method,
        "payment_plan": dec.payment_plan,
        "earliest_date_for_full_payment": dec.earliest_date_for_full_payment,
        "spending_changes_needed": dec.spending_changes_needed,
        "decision_explanation": explanation,
    }


# ==========================================================================
# per-row validation
# ==========================================================================


def validate_row(row: dict, req: Request, ds: Dataset,
                 rep: ValidationReport) -> list[str]:
    """Returns the list of failed rule names. Does not modify the row."""
    fails: list[str] = []
    rid = row["request_id"]

    def fail(rule: str, detail: str) -> None:
        fails.append(rule)
        rep.add(rid, rule, detail)

    # --- enums ------------------------------------------------------------
    status = row["affordability_status"]
    method = row["recommended_payment_method"]
    if status not in STATUSES:
        fail("status_enum", status)
    if method not in METHODS:
        fail("method_enum", method)

    # --- amount bounds ----------------------------------------------------
    try:
        safe = float(row["amount_safe_to_pay"])
    except ValueError:
        fail("safe_numeric", row["amount_safe_to_pay"])
        safe = 0.0
    if safe < -1e-9:
        fail("safe_negative", str(safe))
    if safe > req.requested_amount + 0.01:
        fail("safe_exceeds_requested",
             f"{safe} > {req.requested_amount}")

    # --- earliest date ----------------------------------------------------
    earliest = row["earliest_date_for_full_payment"]
    if earliest and not DATE_RE.match(earliest):
        fail("earliest_date_format", earliest)
    if status == "affordable_now" and earliest != req.request_date.isoformat():
        fail("affordable_now_earliest_is_request_date", earliest)
    if status == "not_affordable" and earliest != "":
        fail("not_affordable_earliest_blank", earliest)
    if status in ("affordable_with_plan", "affordable_later") and not earliest:
        # earliest_date_for_full_payment is defined WITHOUT optional spending
        # changes. So a plan that only works *because* of a spending change can
        # legitimately pair `affordable_with_plan` with a blank earliest date:
        # the full amount never clears on its own inside the horizon. The 25
        # labelled samples never exercise this, but the rule permits it, so it
        # is accepted when a spending change is present and flagged otherwise.
        if row.get("spending_changes_needed", "none") == "none":
            fail("earliest_blank_without_spending_change", status)

    # --- method / status consistency --------------------------------------
    expected = {"affordable_now": {"full_payment"},
                "affordable_later": {"wait"},
                "not_affordable": {"not_recommended"},
                "affordable_with_plan": {"full_payment", "partial_payment",
                                         "installments"}}
    if status in expected and method not in expected[status]:
        fail("status_method_mismatch", f"{status}/{method}")

    # --- eligibility ------------------------------------------------------
    accepts = ds.profiles[req.user_id].payment_methods_user_will_consider
    if method in ("full_payment", "partial_payment", "installments") \
            and method not in accepts:
        fail("method_not_accepted", f"{method} not in {accepts}")
    if method == "wait" and "full_payment" not in accepts:
        fail("wait_requires_full_payment", str(accepts))

    # --- payment plan -----------------------------------------------------
    plan = row["payment_plan"]
    if method == "not_recommended":
        if plan != "none":
            fail("plan_must_be_none", plan)
    else:
        if plan == "none":
            fail("plan_missing", method)
        else:
            segs = plan.split("|")
            if any(not SEG_RE.match(s) for s in segs):
                fail("plan_format", plan)
            else:
                dates = [date.fromisoformat(s.split(":")[0]) for s in segs]
                if dates != sorted(dates):
                    fail("plan_not_chronological", plan)
                amounts = [float(s.split(":")[1]) for s in segs]
                total = round(sum(amounts), 2)
                if method in ("full_payment", "wait", "partial_payment"):
                    if abs(total - req.requested_amount) > 0.01:
                        fail("plan_sum_mismatch",
                             f"{total} != {req.requested_amount}")
                if method == "partial_payment":
                    if len(segs) != 2:
                        fail("partial_needs_two_payments", str(len(segs)))
                    elif dates[0] != req.request_date:
                        fail("partial_first_is_request_date", str(dates[0]))
                    elif abs(amounts[0] - safe) > 0.01:
                        fail("partial_first_is_safe_amount",
                             f"{amounts[0]} != {safe}")
                    elif not req.allows_partial_payment:
                        fail("partial_not_allowed", rid)
                    elif not (0 < safe < req.requested_amount):
                        fail("partial_bounds", str(safe))
                    elif earliest and date.fromisoformat(earliest) > \
                            req.desired_completion_date:
                        fail("partial_earliest_after_deadline", earliest)
                if method == "installments":
                    opts = ds.options_by_request.get(rid, [])
                    match = [o for o in opts
                             if o.payment_method == "installments"
                             and o.number_of_payments == len(segs)
                             and o.first_payment_date == dates[0]
                             and abs(o.payment_amount - amounts[0]) < 0.01]
                    if not match:
                        fail("installments_no_matching_option", plan)
                if method == "full_payment" and len(segs) != 1:
                    fail("full_payment_single", plan)
                if method == "wait":
                    if len(segs) != 1:
                        fail("wait_single_payment", plan)
                    elif earliest and dates[0] != date.fromisoformat(earliest):
                        fail("wait_date_is_earliest", plan)

    # --- spending changes --------------------------------------------------
    changes = row["spending_changes_needed"]
    if changes != "none":
        parts = changes.split("|")
        if len(parts) > 3:
            fail("too_many_changes", str(len(parts)))
        seen: set[str] = set()
        for p in parts:
            if not CHANGE_RE.match(p):
                fail("change_format", p)
                continue
            bits = p.split(":")
            eid = bits[1]
            if eid in seen:
                fail("stop_and_reduce_same_event", eid)
            seen.add(eid)
            ev = ds.events_by_id.get(eid)
            if ev is None:
                fail("change_unknown_event", eid)
                continue
            if ev.user_id != req.user_id:
                fail("change_wrong_user", eid)
            if bits[0] == "stop" and not ev.is_stoppable:
                fail("change_not_stoppable", f"{eid} {ev.flexibility}")
            if bits[0] == "reduce_to":
                if not ev.is_reducible:
                    fail("change_not_reducible", f"{eid} {ev.flexibility}")
                elif abs(float(bits[2]) - (ev.minimum_allowed_amount or -1)) > 0.01:
                    fail("reduce_to_not_minimum_allowed",
                         f"{bits[2]} != {ev.minimum_allowed_amount}")

    # --- explanation -------------------------------------------------------
    expl = row["decision_explanation"]
    if not expl.strip():
        fail("explanation_empty", "")
    if "\n" in expl or "\r" in expl:
        fail("explanation_newline", "")

    return fails


# ==========================================================================
# repair
# ==========================================================================


def repair(row: dict, dec: Decision, req: Request, ds: Dataset,
           fails: list[str], rep: ValidationReport) -> dict:
    """Deterministic repair. Escalates to the conservative row if needed."""
    from explain import render_fallback
    fixed = dict(row)

    if "safe_exceeds_requested" in fails:
        fixed["amount_safe_to_pay"] = fmt_safe(req.requested_amount)
    if "safe_negative" in fails or "safe_numeric" in fails:
        fixed["amount_safe_to_pay"] = "0"
    if "explanation_empty" in fails or "explanation_newline" in fails:
        fixed["decision_explanation"] = render_fallback(dec).replace("\n", " ")
    if "not_affordable_earliest_blank" in fails:
        fixed["earliest_date_for_full_payment"] = ""
    if "affordable_now_earliest_is_request_date" in fails:
        fixed["earliest_date_for_full_payment"] = req.request_date.isoformat()

    structural = {
        "status_enum", "method_enum", "status_method_mismatch",
        "method_not_accepted", "wait_requires_full_payment", "plan_format",
        "plan_missing", "plan_must_be_none", "plan_not_chronological",
        "plan_sum_mismatch", "installments_no_matching_option",
        "partial_needs_two_payments", "partial_first_is_request_date",
        "partial_first_is_safe_amount", "partial_not_allowed", "partial_bounds",
        "partial_earliest_after_deadline", "full_payment_single",
        "wait_single_payment", "wait_date_is_earliest", "change_format",
        "change_unknown_event", "change_wrong_user", "change_not_stoppable",
        "change_not_reducible", "reduce_to_not_minimum_allowed",
        "too_many_changes", "stop_and_reduce_same_event",
    }
    if structural & set(fails):
        # Do not guess a different plan. Fall back to the safest valid row:
        # recommend nothing, keep the computed safe amount within bounds.
        safe = min(max(0.0, dec.amount_safe_to_pay), req.requested_amount)
        conservative = Decision(
            request_id=req.request_id, amount_safe_to_pay=safe,
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payment_plan="none", earliest_date_for_full_payment="",
            spending_changes_needed="none", currency=dec.currency,
            minimum_balance=dec.minimum_balance,
            requested_amount=req.requested_amount,
            desired_completion_date=req.desired_completion_date.isoformat(),
            request_date=req.request_date.isoformat(), fallback=True)
        fixed = row_from_decision(conservative, render_fallback(conservative))
        rep.repaired.append(f"{req.request_id}: structural -> conservative row")
    else:
        rep.repaired.append(f"{req.request_id}: {','.join(sorted(set(fails)))}")
    return fixed


# ==========================================================================
# file-level validation
# ==========================================================================


def validate_file(path: str, ds: Dataset) -> ValidationReport:
    rep = ValidationReport()
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        rows = list(reader)

    if cols != OUTPUT_COLUMNS:
        rep.add("-", "column_order", str(cols))

    expected_ids = [r.request_id for r in ds.requests]
    got_ids = [r["request_id"] for r in rows]
    if len(rows) != len(expected_ids):
        rep.add("-", "row_count", f"{len(rows)} != {len(expected_ids)}")
    if set(got_ids) != set(expected_ids):
        missing = set(expected_ids) - set(got_ids)
        extra = set(got_ids) - set(expected_ids)
        rep.add("-", "request_id_set",
                f"missing={sorted(missing)[:5]} extra={sorted(extra)[:5]}")
    if len(set(got_ids)) != len(got_ids):
        rep.add("-", "duplicate_rows", "")
    if got_ids != expected_ids:
        rep.add("-", "row_order", "output order differs from requests.csv")

    by_id = {r.request_id: r for r in ds.requests}
    for row in rows:
        req = by_id.get(row["request_id"])
        if req is None:
            continue
        validate_row(row, req, ds, rep)
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="../dataset")
    ap.add_argument("--output", default="../output.csv")
    args = ap.parse_args()
    ds = load_dataset(args.dataset)
    rep = validate_file(args.output, ds)
    print(f"rows checked against {args.dataset}")
    print("RESULT:", "PASS" if rep.ok else f"FAIL - {len(rep.issues)} issues")
    print(rep.summary())
    for i in rep.issues[:40]:
        print(f"  {i.request_id:<14}{i.rule:<38}{i.detail[:60]}")
    sys.exit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
