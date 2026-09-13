"""
test_solve.py - edge-case tests for formatting, ranking, plan rules, evidence
safety and output validation.

These complement test_state.py (which covers reconstruction). Together they are
the regression net for the pieces that actually reach output.csv.

    cd code && python3 -m pytest ../tests -q
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "code"))

from config import DEFAULT                                        # noqa: E402
from evidence import (                                            # noqa: E402
    INCOME_CLASSES,
    classify_income_streams,
)
from solve import (                                               # noqa: E402
    Candidate,
    SpendingChange,
    conservative_fallback,
    fmt_plan,
    fmt_safe,
    rank_key,
)
from state import PaymentOption, load_dataset                     # noqa: E402
from validate import OUTPUT_COLUMNS, ValidationReport, validate_row  # noqa: E402

D = date


# ==========================================================================
# formatting - the two conventions differ, and mixing them loses exact matches
# ==========================================================================


@pytest.mark.parametrize("value,expected", [
    (17229139.2, "17229139.2"),
    (603.3, "603.3"),
    (87170.56, "87170.56"),
    (737.0, "737"),
    (25256, "25256"),
    (620.40, "620.4"),
    (0.0, "0"),
])
def test_fmt_safe_strips_trailing_zeros(value, expected):
    assert fmt_safe(value) == expected


@pytest.mark.parametrize("value,expected", [
    (620.4, "620.40"),
    (996.6, "996.60"),
    (23.5, "23.50"),
    (3246.1, "3246.10"),
    (25256, "25256"),
    (68432.0, "68432"),
    (13110000, "13110000"),
])
def test_fmt_plan_pads_to_two_decimals_when_not_whole(value, expected):
    assert fmt_plan(value) == expected


def test_the_two_formatters_genuinely_differ():
    # This is the trap: 620.4 renders differently in the two columns.
    assert fmt_safe(620.4) == "620.4"
    assert fmt_plan(620.4) == "620.40"


# ==========================================================================
# ranking - one tuple key, in the stated lexicographic order
# ==========================================================================


def cand(method="installments", payments=None, total=1000.0, changes=None,
         option_id="payment_option_01", rank=1) -> Candidate:
    return Candidate(
        method=method,
        payments=payments or [(D(2025, 2, 1), 1000.0)],
        total_paid=total,
        changes=changes or [],
        option_id=option_id,
        option_rank=rank)


DEADLINE = D(2025, 6, 1)


def test_rank_prefers_completing_by_the_desired_date():
    on_time = cand(payments=[(D(2025, 5, 1), 1000.0)])
    late = cand(payments=[(D(2025, 7, 1), 1000.0)], total=900.0)
    assert rank_key(on_time, DEADLINE) < rank_key(late, DEADLINE)


def test_rank_prefers_no_spending_changes_over_a_cheaper_plan():
    clean = cand(total=1100.0)
    with_change = cand(total=1000.0, changes=[
        SpendingChange("event_1", "stop", None, ("k",), "Gym", "gym")])
    assert rank_key(clean, DEADLINE) < rank_key(with_change, DEADLINE)


def test_rank_then_minimises_total_paid():
    cheap = cand(total=1000.0)
    dear = cand(total=1200.0)
    assert rank_key(cheap, DEADLINE) < rank_key(dear, DEADLINE)


def test_rank_then_prefers_an_earlier_start():
    early = cand(payments=[(D(2025, 3, 1), 1000.0)])
    late = cand(payments=[(D(2025, 4, 1), 1000.0)])
    assert rank_key(early, DEADLINE) < rank_key(late, DEADLINE)


def test_rank_then_prefers_fewer_payments():
    few = cand(payments=[(D(2025, 3, 1), 500.0), (D(2025, 4, 1), 500.0)])
    many = cand(payments=[(D(2025, 3, 1), 334.0), (D(2025, 4, 1), 333.0),
                          (D(2025, 5, 1), 333.0)])
    assert rank_key(few, DEADLINE) < rank_key(many, DEADLINE)


def test_rank_finally_prefers_the_lowest_option_id():
    lo = cand(option_id="payment_option_05", rank=5)
    hi = cand(option_id="payment_option_07", rank=7)
    assert rank_key(lo, DEADLINE) < rank_key(hi, DEADLINE)


# ==========================================================================
# plan rendering
# ==========================================================================


def test_plan_renders_in_the_required_pipe_format():
    c = cand(payments=[(D(2025, 2, 8), 100.0), (D(2025, 3, 10), 100.5)])
    assert c.render_plan() == "2025-02-08:100|2025-03-10:100.50"


def test_no_spending_changes_renders_as_the_literal_none():
    assert cand().render_changes() == "none"


def test_reduce_to_renders_with_the_events_minimum_allowed_amount():
    ch = SpendingChange("event_1816", "reduce_to", 23.5, ("k",), "Streaming",
                        "streaming")
    assert ch.render() == "reduce_to:event_1816:23.50"


def test_stop_renders_without_an_amount():
    ch = SpendingChange("event_476", "stop", None, ("k",), "Gym", "gym")
    assert ch.render() == "stop:event_476"


def test_installment_option_schedule_steps_by_frequency_days():
    opt = PaymentOption("payment_option_05", "request_02", "installments",
                        100.0, 3, D(2025, 2, 8), 30, 20.0, 300.0)
    assert opt.schedule() == [(D(2025, 2, 8), 100.0),
                              (D(2025, 3, 10), 100.0),
                              (D(2025, 4, 9), 100.0)]


# ==========================================================================
# evidence layer safety
# ==========================================================================


def test_income_lexicon_marks_a_final_payroll_as_ended():
    ds = load_dataset(os.path.join(HERE, "..", "dataset"))
    req = next(r for r in ds.sample_requests if r.request_id == "request_05")
    signals = classify_income_streams(ds.events_by_user[req.user_id], req)
    by_desc = {s.stream_key: s.classification for s in signals}
    assert by_desc.get("Final employer payroll") == "ends"


def test_income_lexicon_never_emits_a_label_outside_the_closed_enum():
    ds = load_dataset(os.path.join(HERE, "..", "dataset"))
    for req in ds.sample_requests:
        for s in classify_income_streams(ds.events_by_user[req.user_id], req):
            assert s.classification in INCOME_CLASSES


def test_one_off_income_is_never_projected():
    ds = load_dataset(os.path.join(HERE, "..", "dataset"))
    req = ds.sample_requests[0]
    signals = classify_income_streams(ds.events_by_user[req.user_id], req)
    for s in signals:
        if s.stream_key in ("Quarterly performance bonus", "Prize proceeds"):
            assert s.classification == "one_off"


# ==========================================================================
# validation
# ==========================================================================


@pytest.fixture(scope="module")
def real():
    return load_dataset(os.path.join(HERE, "..", "dataset"))


def good_row(req) -> dict:
    return {
        "request_id": req.request_id,
        "amount_safe_to_pay": fmt_safe(req.requested_amount),
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": f"{req.request_date.isoformat()}:"
                        f"{fmt_plan(req.requested_amount)}",
        "earliest_date_for_full_payment": req.request_date.isoformat(),
        "spending_changes_needed": "none",
        "decision_explanation": "Pay today.",
    }


def test_a_well_formed_row_validates(real):
    """validate_row returns a list of broken rule names; empty means valid."""
    req = real.requests[0]
    rep = ValidationReport()
    assert validate_row(good_row(req), req, real, rep) == []


def test_safe_amount_above_requested_is_rejected(real):
    req = real.requests[0]
    row = good_row(req)
    row["amount_safe_to_pay"] = fmt_safe(req.requested_amount + 1)
    rep = ValidationReport()
    assert validate_row(row, req, real, rep)


def test_negative_safe_amount_is_rejected(real):
    req = real.requests[0]
    row = good_row(req)
    row["amount_safe_to_pay"] = "-1"
    rep = ValidationReport()
    assert validate_row(row, req, real, rep)


def test_unknown_status_is_rejected(real):
    req = real.requests[0]
    row = good_row(req)
    row["affordability_status"] = "probably_fine"
    rep = ValidationReport()
    assert validate_row(row, req, real, rep)


def test_affordable_now_must_have_earliest_equal_to_request_date(real):
    req = real.requests[0]
    row = good_row(req)
    row["earliest_date_for_full_payment"] = "2099-01-01"
    rep = ValidationReport()
    assert validate_row(row, req, real, rep)


def test_not_affordable_must_have_a_blank_earliest_date(real):
    req = real.requests[0]
    row = good_row(req)
    row.update({"affordability_status": "not_affordable",
                "recommended_payment_method": "not_recommended",
                "payment_plan": "none",
                "earliest_date_for_full_payment": "2025-01-01",
                "amount_safe_to_pay": "0"})
    rep = ValidationReport()
    assert validate_row(row, req, real, rep)


def test_status_method_mismatch_is_rejected(real):
    req = real.requests[0]
    row = good_row(req)
    row["recommended_payment_method"] = "installments"
    rep = ValidationReport()
    assert validate_row(row, req, real, rep)


def test_malformed_payment_plan_is_rejected(real):
    req = real.requests[0]
    row = good_row(req)
    row["payment_plan"] = "2025/01/01:100"
    rep = ValidationReport()
    assert validate_row(row, req, real, rep)


def test_conservative_fallback_row_is_always_valid(real):
    req = real.requests[0]
    dec = conservative_fallback(req, reason="synthetic failure")
    assert dec.amount_safe_to_pay == 0.0
    assert dec.affordability_status == "not_affordable"
    assert dec.recommended_payment_method == "not_recommended"


# ==========================================================================
# the produced file
# ==========================================================================


def test_output_csv_is_present_and_well_formed(real):
    """Guards the deliverable itself, not just the code that writes it."""
    import csv
    path = os.path.join(HERE, "..", "output.csv")
    if not os.path.exists(path):
        pytest.skip("output.csv not generated yet; run code/main.py")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fh.seek(0)
        header = next(csv.reader(fh))

    assert header == OUTPUT_COLUMNS
    assert len(rows) == len(real.requests)
    assert [r["request_id"] for r in rows] == [r.request_id for r in real.requests]

    by_id = {r.request_id: r for r in real.requests}
    for row in rows:
        req = by_id[row["request_id"]]
        safe = float(row["amount_safe_to_pay"])
        assert 0.0 <= safe <= req.requested_amount + 1e-6
        assert "\n" not in row["decision_explanation"]
        if row["affordability_status"] == "not_affordable":
            assert row["payment_plan"] == "none"
            assert row["earliest_date_for_full_payment"] == ""
        if row["affordability_status"] == "affordable_now":
            assert (row["earliest_date_for_full_payment"]
                    == req.request_date.isoformat())
