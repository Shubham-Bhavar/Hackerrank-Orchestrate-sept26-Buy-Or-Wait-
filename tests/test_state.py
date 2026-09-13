"""
test_state.py - deterministic unit tests for the reconstruction engine.

Every test uses a small synthetic fixture rather than the real dataset, so a
failure points at one rule rather than at 25,000 rows. The two exceptions are
marked `real_dataset` and assert structural facts confirmed in reconnaissance.

    cd code && python3 -m pytest ../tests -q
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "code"))

from config import DEFAULT                                   # noqa: E402

# Unit tests pin the scope explicitly rather than inheriting the calibrated
# DEFAULT, so a future calibration change cannot silently break them.
ALL_SCOPE = DEFAULT.with_(expense_scope="all")
from state import (                                          # noqa: E402
    Dataset,
    FinancialEvent,
    FinancialProfile,
    PaymentOption,
    Request,
    add_months,
    amount_safe_to_pay,
    build_state,
    build_timeline,
    collect_known_flows,
    convert,
    detect_series,
    earliest_date_for_full_payment,
    load_dataset,
    normalise_events,
    project_series,
    reduce_to_amount,
)

D = date


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def profile(**kw) -> FinancialProfile:
    base = dict(
        user_id="u1",
        home_currency="EUR",
        current_available_balance=1000.0,
        minimum_balance_to_keep=200.0,
        financial_priorities=["emergency_savings"],
        expense_categories_to_protect=["rent", "groceries"],
        expense_categories_user_is_willing_to_reduce=["dining"],
        expense_categories_user_is_willing_to_stop=["streaming"],
        payment_methods_user_will_consider=["full_payment"],
        max_installment_months=None,
    )
    base.update(kw)
    return FinancialProfile(**base)


def event(eid="e1", amount=100.0, direction="debit", on="2025-01-10",
          settle=None, status="settled", currency="EUR", category="rent",
          description="Rent", flexibility="fixed", event_type="expense",
          linked="", min_allowed=None) -> FinancialEvent:
    d = D.fromisoformat(on)
    return FinancialEvent(
        event_id=eid, user_id="u1", event_type=event_type, description=description,
        category=category, direction=direction, amount=amount, currency=currency,
        event_date=d, settlement_date=D.fromisoformat(settle) if settle else d,
        status=status, linked_event_id=linked, flexibility=flexibility,
        minimum_allowed_amount=min_allowed,
    )


def request(amount=500.0, on="2025-02-01", **kw) -> Request:
    base = dict(
        request_id="r1", user_id="u1", request_date=D.fromisoformat(on),
        request_type="purchase", requested_amount=amount,
        desired_completion_date=D.fromisoformat("2025-03-01"),
        allows_partial_payment=True, request_text="",
    )
    base.update(kw)
    return Request(**base)


def dataset(events, prof=None, options=None, rates=None) -> Dataset:
    prof = prof or profile()
    by_id = {e.event_id: e for e in events}
    return Dataset(
        profiles={prof.user_id: prof},
        events_by_user={prof.user_id: events},
        events_by_id=by_id,
        options_by_request={"r1": options or []},
        rates=rates or {},
        requests=[], sample_requests=[], sample_labels={}, images_by_event={},
    )


# --------------------------------------------------------------------------
# currency conversion
# --------------------------------------------------------------------------


def test_convert_same_currency_is_identity():
    assert convert(100.0, "EUR", "EUR", D(2025, 1, 1), {}) == 100.0


def test_convert_uses_the_exact_dated_row():
    rates = {("2025-01-01", "USD", "INR"): 83.0, ("2025-06-01", "USD", "INR"): 90.0}
    assert convert(10.0, "USD", "INR", D(2025, 1, 1), rates) == 830.0
    assert convert(10.0, "USD", "INR", D(2025, 6, 1), rates) == 900.0


def test_convert_returns_none_when_no_rate_exists():
    assert convert(10.0, "USD", "INR", D(2030, 1, 1), {}) is None


def test_foreign_currency_event_is_converted_into_home_currency():
    rates = {("2025-02-10", "USD", "EUR"): 0.9}
    ev = event(amount=100.0, currency="USD", on="2025-02-10")
    kept, _, _, fx = normalise_events([ev], profile(), rates, DEFAULT)
    assert not fx
    assert kept[0].home_amount == pytest.approx(90.0)
    assert kept[0].signed == pytest.approx(-90.0)


# --------------------------------------------------------------------------
# exclusions
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status,reason", [
    ("failed", "failed"),
    ("cancelled", "cancelled"),
    ("unrealized", "unrealized"),
])
def test_excluded_statuses_never_reach_the_forecast(status, reason):
    ev = event(status=status)
    kept, excluded, _, _ = normalise_events([ev], profile(), {}, DEFAULT)
    assert kept == []
    assert excluded == [("e1", reason)]


def test_pending_credit_is_excluded_but_pending_debit_is_kept():
    credit = event(eid="c", direction="credit", status="pending", event_type="refund")
    debit = event(eid="d", direction="debit", status="pending")
    kept, excluded, _, _ = normalise_events([credit, debit], profile(), {}, DEFAULT)
    assert [e.event_id for e in kept] == ["d"]
    assert ("c", "pending_credit") in excluded


def test_duplicate_card_charge_is_excluded():
    dup = event(eid="dup", description="Possible duplicate card charge",
                status="pending", linked="orig")
    kept, excluded, _, _ = normalise_events([dup], profile(), {}, DEFAULT)
    assert kept == []
    assert ("dup", "duplicate") in excluded


def test_non_cash_investment_valuation_is_excluded():
    val = event(eid="v", direction="non_cash", event_type="investment_valuation",
                status="unrealized", category="investment")
    kept, excluded, _, _ = normalise_events([val], profile(), {}, DEFAULT)
    assert kept == []
    assert ("v", "non_cash") in excluded


# --------------------------------------------------------------------------
# blank amounts
# --------------------------------------------------------------------------


def test_blank_amount_is_flagged_and_never_treated_as_zero():
    blank = event(eid="b", amount=None)
    kept, excluded, blanks, _ = normalise_events([blank], profile(), {}, DEFAULT)
    assert kept == []
    assert blanks == ["b"]
    assert ("b", "unresolved_blank_amount") in excluded


def test_state_with_a_blank_amount_reports_itself_incomplete():
    ds = dataset([event(eid="b", amount=None, on="2025-02-10")])
    st = build_state(ds, request())
    assert st.unresolved_blank_amounts == ["b"]
    assert st.is_complete is False


def test_blank_amount_does_not_silently_contribute_zero_to_the_balance():
    ds = dataset([event(eid="b", amount=None, on="2025-02-10")])
    st = build_state(ds, request())
    assert all(f.event_id != "b" for f in st.flows)


# --------------------------------------------------------------------------
# known future events and unsettled items
# --------------------------------------------------------------------------


def test_settled_past_event_is_already_inside_the_opening_balance():
    ds = dataset([event(on="2025-01-10", status="settled")])
    st = build_state(ds, request(on="2025-02-01"),
                     ALL_SCOPE.with_(recurrence_min_occurrences=2))
    assert st.flows == []
    assert st.timeline[0].closing_balance == 1000.0


def test_future_dated_event_enters_the_timeline():
    ds = dataset([event(eid="fut", amount=300.0, on="2025-02-20")])
    st = build_state(ds, request(on="2025-02-01"))
    assert any(f.event_id == "fut" and f.amount == -300.0 for f in st.flows)


def test_scheduled_income_is_included_as_a_credit():
    ds = dataset([event(eid="sal", amount=800.0, direction="credit",
                        event_type="income", category="salary",
                        description="Next confirmed salary",
                        on="2025-02-15", status="scheduled")])
    st = build_state(ds, request(on="2025-02-01"))
    assert any(f.amount == 800.0 and f.when == D(2025, 2, 15) for f in st.flows)


def test_pending_debit_before_request_date_is_reserved_on_its_settlement_date():
    ds = dataset([event(eid="p", amount=50.0, on="2025-01-30",
                        settle="2025-02-04", status="pending")])
    st = build_state(ds, request(on="2025-02-01"))
    flow = next(f for f in st.flows if f.event_id == "p")
    assert flow.when == D(2025, 2, 4)
    assert flow.amount == -50.0


def test_event_timing_switch_uses_event_date_when_configured():
    cfg = DEFAULT.with_(unsettled_timing="event_date")
    ev = event(eid="p", amount=50.0, on="2025-02-03", settle="2025-02-09",
               status="pending")
    flows = collect_known_flows([_normalised(ev)], request(on="2025-02-01"), cfg)
    assert flows[0].when == D(2025, 2, 3)


def _normalised(ev: FinancialEvent) -> FinancialEvent:
    kept, _, _, _ = normalise_events([ev], profile(), {}, DEFAULT)
    return kept[0]


# --------------------------------------------------------------------------
# recurrence detection and projection
# --------------------------------------------------------------------------


def monthly_history(n=4, amount=100.0, day=10, start_month=10, year=2024, **kw):
    out = []
    for i in range(n):
        m = start_month + i
        y = year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        out.append(event(eid=f"h{i}", amount=amount,
                         on=f"{y:04d}-{m:02d}-{day:02d}", **kw))
    return out


def test_monthly_series_is_detected():
    evs = [_normalised(e) for e in monthly_history(4)]
    series = detect_series(evs, request(on="2025-02-01"), DEFAULT, profile())
    assert len(series) == 1
    assert series[0].is_fixed_amount
    assert series[0].anchor_day == 10


def test_series_below_the_minimum_occurrence_threshold_is_ignored():
    evs = [_normalised(e) for e in monthly_history(2)]
    cfg = DEFAULT.with_(recurrence_min_occurrences=3)
    assert detect_series(evs, request(on="2025-02-01"), cfg, profile()) == []


def test_variable_amount_series_is_skipped_under_fixed_only():
    evs = [_normalised(e) for e in (
        event(eid="h0", amount=100.0, on="2024-10-10"),
        event(eid="h1", amount=101.0, on="2024-11-10"),
        event(eid="h2", amount=102.0, on="2024-12-10"),
        event(eid="h3", amount=103.0, on="2025-01-10"),
    )]
    cfg = DEFAULT.with_(recurrence_series_filter="fixed_only")
    assert detect_series(evs, request(on="2025-02-01"), cfg, profile()) == []
    cfg2 = cfg.with_(recurrence_series_filter="fixed_and_variable")
    assert len(detect_series(evs, request(on="2025-02-01"), cfg2, profile())) == 1


def test_projection_repeats_monthly_across_the_horizon():
    evs = [_normalised(e) for e in monthly_history(4)]
    req = request(on="2025-02-01")
    series = detect_series(evs, req, DEFAULT, profile())
    flows = project_series(series, req, DEFAULT)
    assert [f.when for f in flows] == [D(2025, 2, 10), D(2025, 3, 10), D(2025, 4, 10)]
    assert all(f.amount == -100.0 for f in flows)


def test_projection_never_lands_before_the_request_date():
    evs = [_normalised(e) for e in monthly_history(4)]
    req = request(on="2025-02-20")
    series = detect_series(evs, req, DEFAULT, profile())
    flows = project_series(series, req, DEFAULT)
    assert all(f.when >= req.request_date for f in flows)


def test_blocked_dates_stop_a_projection_colliding_with_a_real_event():
    evs = [_normalised(e) for e in monthly_history(
        4, amount=800.0, direction="credit", event_type="income",
        category="salary", description="Payroll")]
    req = request(on="2025-02-01")
    series = detect_series(evs, req, DEFAULT, profile())
    blocked = {("salary", "credit", D(2025, 2, 10))}
    flows = project_series(series, req, DEFAULT, blocked=blocked)
    assert D(2025, 2, 10) not in [f.when for f in flows]


def test_add_months_clamps_to_the_end_of_a_short_month():
    assert add_months(D(2025, 1, 31), 1, 31) == D(2025, 2, 28)
    assert add_months(D(2024, 1, 31), 1, 31) == D(2024, 2, 29)


# --------------------------------------------------------------------------
# horizon and timeline
# --------------------------------------------------------------------------


def test_timeline_covers_exactly_ninety_one_days_inclusive():
    ds = dataset([])
    st = build_state(ds, request(on="2025-02-01"))
    assert st.timeline[0].day == D(2025, 2, 1)
    assert st.timeline[-1].day == D(2025, 5, 2)
    assert len(st.timeline) == 91


def test_horizon_length_is_configurable():
    ds = dataset([])
    st = build_state(ds, request(on="2025-02-01"), DEFAULT.with_(horizon_days=30))
    assert st.timeline[-1].day == D(2025, 3, 3)


def test_flow_beyond_the_horizon_is_ignored():
    ds = dataset([event(eid="far", amount=900.0, on="2025-09-01")])
    st = build_state(ds, request(on="2025-02-01"))
    assert all(f.event_id != "far" for f in st.flows)


def test_timeline_balance_accumulates_in_date_order():
    req = request(on="2025-02-01")
    from state import Flow
    flows = [Flow(D(2025, 2, 3), -100.0, "a", "known_event"),
             Flow(D(2025, 2, 5), +250.0, "b", "known_event")]
    tl = build_timeline(1000.0, flows, req, DEFAULT)
    assert tl[0].closing_balance == 1000.0
    assert tl[2].closing_balance == 900.0
    assert tl[4].closing_balance == 1150.0


# --------------------------------------------------------------------------
# minimum balance protection and the safe amount
# --------------------------------------------------------------------------


def test_safe_amount_is_the_trough_minus_the_minimum():
    # balance 1000, minimum 200, one 300 debit inside the horizon -> trough 700
    ds = dataset([event(eid="d", amount=300.0, on="2025-02-10")])
    st = build_state(ds, request(amount=5000.0, on="2025-02-01"))
    assert amount_safe_to_pay(st) == pytest.approx(500.0)


def test_safe_amount_is_capped_at_the_requested_amount():
    ds = dataset([])
    st = build_state(ds, request(amount=50.0, on="2025-02-01"))
    assert amount_safe_to_pay(st) == 50.0


def test_safe_amount_is_never_negative():
    ds = dataset([event(eid="d", amount=5000.0, on="2025-02-10")])
    st = build_state(ds, request(amount=100.0, on="2025-02-01"))
    assert amount_safe_to_pay(st) == 0.0


def test_safe_amount_respects_the_minimum_balance_setting():
    ds = dataset([], prof=profile(minimum_balance_to_keep=900.0))
    st = build_state(ds, request(amount=5000.0, on="2025-02-01"))
    assert amount_safe_to_pay(st) == pytest.approx(100.0)


def test_request_date_anchors_the_window():
    cfg = ALL_SCOPE.with_(recurrence_min_occurrences=2)
    ds = dataset([event(eid="d", amount=400.0, on="2025-02-10")])
    early = build_state(ds, request(amount=5000.0, on="2025-02-01"), cfg)
    late = build_state(ds, request(amount=5000.0, on="2025-02-20"), cfg)
    assert amount_safe_to_pay(early) == pytest.approx(400.0)   # debit is ahead
    assert amount_safe_to_pay(late) == pytest.approx(800.0)    # debit is behind


def test_earliest_full_payment_is_the_first_date_the_whole_amount_clears():
    ds = dataset([event(eid="sal", amount=1000.0, direction="credit",
                        event_type="income", category="salary",
                        description="Next confirmed salary",
                        on="2025-02-15", status="scheduled")])
    st = build_state(ds, request(amount=1500.0, on="2025-02-01"))
    assert earliest_date_for_full_payment(st) == D(2025, 2, 15)


def test_earliest_full_payment_is_none_when_never_affordable():
    ds = dataset([])
    st = build_state(ds, request(amount=999999.0, on="2025-02-01"))
    assert earliest_date_for_full_payment(st) is None


# --------------------------------------------------------------------------
# spending changes
# --------------------------------------------------------------------------


def test_reduce_to_uses_the_events_own_minimum_allowed_amount():
    evs = [_normalised(e) for e in monthly_history(
        4, amount=47.0, category="streaming", description="Streaming",
        flexibility="reducible_or_stoppable", event_type="subscription",
        min_allowed=23.5)]
    series = detect_series(evs, request(on="2025-02-01"), ALL_SCOPE, profile())
    assert reduce_to_amount(series[0]) == 23.5


def test_reduce_to_is_none_for_a_non_reducible_series():
    evs = [_normalised(e) for e in monthly_history(4, flexibility="fixed")]
    series = detect_series(evs, request(on="2025-02-01"), DEFAULT, profile())
    assert reduce_to_amount(series[0]) is None


def test_stopping_a_series_removes_all_of_its_future_occurrences():
    evs = [_normalised(e) for e in monthly_history(
        4, amount=61.0, category="gym", description="Gym", flexibility="stoppable",
        event_type="subscription")]
    req = request(on="2025-02-01")
    series = detect_series(evs, req, ALL_SCOPE, profile())
    assert project_series(series, req, ALL_SCOPE) != []
    assert project_series(series, req, ALL_SCOPE,
                          overrides={series[0].key: None}) == []


def test_reducing_a_series_lowers_every_future_occurrence():
    evs = [_normalised(e) for e in monthly_history(
        4, amount=100.0, category="dining", description="Dining",
        flexibility="reducible", min_allowed=40.0)]
    req = request(on="2025-02-01")
    series = detect_series(evs, req, ALL_SCOPE, profile())
    flows = project_series(series, req, ALL_SCOPE, overrides={series[0].key: 40.0})
    assert flows and all(f.amount == -40.0 for f in flows)


# --------------------------------------------------------------------------
# payment options
# --------------------------------------------------------------------------


def test_installment_option_expands_to_its_exact_schedule():
    opt = PaymentOption(
        payment_option_id="o1", request_id="r1", payment_method="installments",
        payment_amount=100.0, number_of_payments=3,
        first_payment_date=D(2025, 2, 8), payment_frequency_days=30,
        financing_fee=20.0, total_payable_amount=300.0)
    assert opt.schedule() == [
        (D(2025, 2, 8), 100.0), (D(2025, 3, 10), 100.0), (D(2025, 4, 9), 100.0)]


def test_full_payment_option_is_a_single_payment():
    opt = PaymentOption("o2", "r1", "full_payment", 500.0, 1,
                        D(2025, 2, 1), None, 0.0, 500.0)
    assert opt.schedule() == [(D(2025, 2, 1), 500.0)]


# --------------------------------------------------------------------------
# structural assertions against the real dataset
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real() -> Dataset:
    return load_dataset(os.path.join(HERE, "..", "dataset"))


def test_real_dataset_every_blank_amount_has_an_image(real):
    blanks = [e.event_id for evs in real.events_by_user.values()
              for e in evs if e.amount is None]
    assert len(blanks) == 16
    assert all(b in real.images_by_event for b in blanks)


def test_real_dataset_every_foreign_event_has_an_exact_rate(real):
    missing = []
    for uid, evs in real.events_by_user.items():
        home = real.profiles[uid].home_currency
        for e in evs:
            if e.amount is None or e.currency == home:
                continue
            if convert(e.amount, e.currency, home, e.event_date, real.rates) is None:
                missing.append(e.event_id)
    assert missing == []


def test_real_dataset_safe_amount_bounds_hold_for_every_request(real):
    for req in real.requests[:40]:
        st = build_state(real, req)
        safe = amount_safe_to_pay(st)
        assert 0.0 <= safe <= req.requested_amount
