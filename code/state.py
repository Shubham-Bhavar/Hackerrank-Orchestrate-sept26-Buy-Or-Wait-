"""
state.py - deterministic financial-state reconstruction.

No LLM. No heuristics that produce a number the scorer will see. Everything in
this module is arithmetic over the dataset plus the named assumptions in
config.py.

The pipeline for one request:

    load_dataset()                 read the seven CSVs once
      -> build_state(request)      per-user reconstruction
           normalise_events()        FX + status filtering + duplicate removal
           collect_known_flows()     unsettled + future-dated real events
           detect_series()           find recurring commitments in history
           project_series()          extend them across the horizon
           build_timeline()          day-by-day balance array
      -> headroom(state)           min(balance - minimum_balance_to_keep)
      -> amount_safe_to_pay()      clamp(headroom, 0, requested_amount)
      -> earliest_full_payment()   first date the whole amount clears

A blank event amount is NEVER treated as zero. It is recorded in
state.unresolved_blank_amounts and the state is marked incomplete.
"""

from __future__ import annotations

import csv
import math
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from config import (
    DEFAULT,
    ESSENTIAL_CATEGORIES,
    DUPLICATE_MARKERS,
    NON_CASH_DIRECTIONS,
    NON_CASH_EVENT_TYPES,
    REDUCIBLE_FLEXIBILITY,
    REVERSAL_MARKERS,
    STOPPABLE_FLEXIBILITY,
    ReconstructionConfig,
)

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def parse_money(s: str) -> float | None:
    """Returns None for a blank amount. NEVER returns 0.0 for a blank."""
    s = (s or "").strip()
    if s == "":
        return None
    return float(s)


def parse_list(s: str) -> list[str]:
    return [t.strip() for t in (s or "").split("|") if t.strip()]


def parse_bool(s: str) -> bool:
    return (s or "").strip().lower() == "true"


def add_months(d: date, n: int, anchor_day: int) -> date:
    """Move n months forward, landing on anchor_day (clamped to month length)."""
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    last_day = (next_month_start - timedelta(days=1)).day
    return date(year, month, min(anchor_day, last_day))


def money(x: float, dp: int = 2) -> float:
    return round(x + 0.0, dp)


# --------------------------------------------------------------------------
# typed structures
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: list[str]
    expense_categories_to_protect: list[str]
    expense_categories_user_is_willing_to_reduce: list[str]
    expense_categories_user_is_willing_to_stop: list[str]
    payment_methods_user_will_consider: list[str]
    max_installment_months: int | None

    def accepts(self, method: str) -> bool:
        return method in self.payment_methods_user_will_consider


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str            # debit | credit | non_cash
    amount: float | None      # None == blank in the CSV, must be resolved
    currency: str
    event_date: date
    settlement_date: date | None
    status: str
    linked_event_id: str
    flexibility: str
    minimum_allowed_amount: float | None

    # filled in by normalisation
    home_amount: float | None = None   # amount converted to the user's currency
    excluded_reason: str | None = None

    @property
    def signed(self) -> float:
        """Cash effect in home currency. Positive = money in."""
        if self.home_amount is None:
            return 0.0
        return self.home_amount if self.direction == "credit" else -self.home_amount

    @property
    def is_stoppable(self) -> bool:
        return self.flexibility in STOPPABLE_FLEXIBILITY

    @property
    def is_reducible(self) -> bool:
        return (
            self.flexibility in REDUCIBLE_FLEXIBILITY
            and self.minimum_allowed_amount is not None
        )


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: float
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: float
    total_payable_amount: float

    def schedule(self) -> list[tuple[date, float]]:
        """The exact dates and amounts this option implies."""
        if self.number_of_payments <= 1 or not self.payment_frequency_days:
            return [(self.first_payment_date, self.payment_amount)]
        return [
            (
                self.first_payment_date + timedelta(days=self.payment_frequency_days * i),
                self.payment_amount,
            )
            for i in range(self.number_of_payments)
        ]


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: float
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass
class RecurringSeries:
    """A commitment detected by repetition in the user's history."""

    key: tuple[str, str, str]          # (description, category, direction)
    description: str
    category: str
    direction: str
    event_type: str
    flexibility: str
    representative_event_id: str       # the most recent occurrence; what stop:/reduce_to: points at
    minimum_allowed_amount: float | None
    occurrences: list[date]
    amounts: list[float]               # in home currency
    is_fixed_amount: bool
    cadence_days: float
    anchor_day: int
    projected_amount: float

    @property
    def is_stoppable(self) -> bool:
        return self.flexibility in STOPPABLE_FLEXIBILITY

    @property
    def is_reducible(self) -> bool:
        return (
            self.flexibility in REDUCIBLE_FLEXIBILITY
            and self.minimum_allowed_amount is not None
        )


@dataclass
class Flow:
    """One cash movement on the timeline."""

    when: date
    amount: float          # signed, home currency
    label: str
    source: str            # known_event | projected | pending_reserve
    event_id: str = ""
    series_key: tuple | None = None


@dataclass
class TimelineEntry:
    day: date
    inflow: float
    outflow: float
    closing_balance: float
    flows: list[Flow] = field(default_factory=list)

    @property
    def headroom(self) -> float:
        return self.closing_balance


@dataclass
class FinancialState:
    request: Request
    profile: FinancialProfile
    config: ReconstructionConfig
    opening_balance: float
    flows: list[Flow]
    timeline: list[TimelineEntry]
    series: list[RecurringSeries]
    payment_options: list[PaymentOption]
    excluded: list[tuple[str, str]] = field(default_factory=list)   # (event_id, reason)
    unresolved_blank_amounts: list[str] = field(default_factory=list)
    fx_failures: list[str] = field(default_factory=list)
    evidence_notes: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.unresolved_blank_amounts and not self.fx_failures

    @property
    def minimum(self) -> float:
        return self.profile.minimum_balance_to_keep

    def balance_on(self, d: date) -> float:
        last = self.opening_balance
        for e in self.timeline:
            if e.day > d:
                break
            last = e.closing_balance
        return last

    def headroom(self, start: date | None = None) -> float:
        """min(balance - minimum) across the horizon, from `start` onwards."""
        start = start or self.request.request_date
        worst = None
        for e in self.timeline:
            if e.day < start:
                continue
            gap = e.closing_balance - self.minimum
            worst = gap if worst is None else min(worst, gap)
        if worst is None:
            return self.opening_balance - self.minimum
        return worst

    def worst_day(self, start: date | None = None) -> date | None:
        start = start or self.request.request_date
        worst_day, worst = None, None
        for e in self.timeline:
            if e.day < start:
                continue
            gap = e.closing_balance - self.minimum
            if worst is None or gap < worst:
                worst, worst_day = gap, e.day
        return worst_day


# --------------------------------------------------------------------------
# dataset loading
# --------------------------------------------------------------------------


def _read(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Dataset:
    profiles: dict[str, FinancialProfile]
    events_by_user: dict[str, list[FinancialEvent]]
    events_by_id: dict[str, FinancialEvent]
    options_by_request: dict[str, list[PaymentOption]]
    rates: dict[tuple[str, str, str], float]
    requests: list[Request]
    sample_requests: list[Request]
    sample_labels: dict[str, dict]
    images_by_event: dict[str, str]
    messages_by_user: dict[str, list[dict]] = field(default_factory=dict)
    dataset_dir: str = "dataset"


def load_dataset(dataset_dir: str = "dataset") -> Dataset:
    p = lambda n: os.path.join(dataset_dir, n)  # noqa: E731

    profiles = {}
    for r in _read(p("financial_profiles.csv")):
        mim = r["max_installment_months"].strip()
        profiles[r["user_id"]] = FinancialProfile(
            user_id=r["user_id"],
            home_currency=r["home_currency"],
            current_available_balance=float(r["current_available_balance"]),
            minimum_balance_to_keep=float(r["minimum_balance_to_keep"]),
            financial_priorities=parse_list(r["financial_priorities"]),
            expense_categories_to_protect=parse_list(r["expense_categories_to_protect"]),
            expense_categories_user_is_willing_to_reduce=parse_list(
                r["expense_categories_user_is_willing_to_reduce"]),
            expense_categories_user_is_willing_to_stop=parse_list(
                r["expense_categories_user_is_willing_to_stop"]),
            payment_methods_user_will_consider=parse_list(
                r["payment_methods_user_will_consider"]),
            max_installment_months=int(mim) if mim else None,
        )

    events_by_user: dict[str, list[FinancialEvent]] = defaultdict(list)
    events_by_id: dict[str, FinancialEvent] = {}
    for r in _read(p("financial_events.csv")):
        ev = FinancialEvent(
            event_id=r["event_id"],
            user_id=r["user_id"],
            event_type=r["event_type"],
            description=r["description"],
            category=r["category"],
            direction=r["direction"],
            amount=parse_money(r["amount"]),
            currency=r["currency"],
            event_date=parse_date(r["event_date"]),
            settlement_date=parse_date(r["settlement_date"]),
            status=r["status"],
            linked_event_id=r["linked_event_id"].strip(),
            flexibility=r["flexibility"],
            minimum_allowed_amount=parse_money(r["minimum_allowed_amount"]),
        )
        events_by_user[ev.user_id].append(ev)
        events_by_id[ev.event_id] = ev

    options_by_request: dict[str, list[PaymentOption]] = defaultdict(list)
    for r in _read(p("request_payment_options.csv")):
        freq = r["payment_frequency_days"].strip()
        options_by_request[r["request_id"]].append(PaymentOption(
            payment_option_id=r["payment_option_id"],
            request_id=r["request_id"],
            payment_method=r["payment_method"],
            payment_amount=float(r["payment_amount"]),
            number_of_payments=int(r["number_of_payments"]),
            first_payment_date=parse_date(r["first_payment_date"]),
            payment_frequency_days=int(freq) if freq else None,
            financing_fee=float(r["financing_fee"]),
            total_payable_amount=float(r["total_payable_amount"]),
        ))

    rates = {}
    for r in _read(p("exchange_rates.csv")):
        rates[(r["rate_date"], r["from_currency"], r["to_currency"])] = float(r["rate"])

    def to_request(r: dict) -> Request:
        return Request(
            request_id=r["request_id"],
            user_id=r["user_id"],
            request_date=parse_date(r["request_date"]),
            request_type=r["request_type"],
            requested_amount=float(r["requested_amount"]),
            desired_completion_date=parse_date(r["desired_completion_date"]),
            allows_partial_payment=parse_bool(r["allows_partial_payment"]),
            request_text=r["request_text"],
        )

    requests = [to_request(r) for r in _read(p("requests.csv"))]

    sample_rows = _read(p("sample_requests.csv"))
    sample_requests = [to_request(r) for r in sample_rows]
    sample_labels = {r["request_id"]: r for r in sample_rows}

    images_by_event = {}
    for r in _read(p("images.csv")):
        images_by_event[r["related_event_id"]] = r["image_id"]

    messages_by_user: dict[str, list[dict]] = defaultdict(list)
    for r in _read(p("messages.csv")):
        messages_by_user[r["user_id"]].append(r)

    return Dataset(
        profiles=profiles,
        events_by_user=dict(events_by_user),
        events_by_id=events_by_id,
        options_by_request=dict(options_by_request),
        rates=rates,
        requests=requests,
        sample_requests=sample_requests,
        sample_labels=sample_labels,
        images_by_event=images_by_event,
        messages_by_user=dict(messages_by_user),
        dataset_dir=dataset_dir,
    )


# --------------------------------------------------------------------------
# currency
# --------------------------------------------------------------------------


def convert(amount: float, frm: str, to: str, on: date,
            rates: dict[tuple[str, str, str], float]) -> float | None:
    """Exact dated conversion. Returns None if no rate row exists.

    Reconnaissance confirmed all 140 foreign-currency events resolve on the
    exact (event_date, from, to) key, so no inversion or interpolation is
    implemented. The inverse lookup below is a safety net only.
    """
    if frm == to:
        return amount
    key = (on.isoformat(), frm, to)
    if key in rates:
        return amount * rates[key]
    inverse = (on.isoformat(), to, frm)
    if inverse in rates and rates[inverse]:
        return amount / rates[inverse]
    return None


# --------------------------------------------------------------------------
# step 1: normalise (FX + exclusions)
# --------------------------------------------------------------------------


def _is_duplicate_row(ev: FinancialEvent) -> bool:
    d = ev.description.lower()
    return bool(ev.linked_event_id) and any(m in d for m in DUPLICATE_MARKERS)


def _is_reversal_row(ev: FinancialEvent) -> bool:
    d = ev.description.lower()
    return bool(ev.linked_event_id) and any(m in d for m in REVERSAL_MARKERS)


def normalise_events(events: Iterable[FinancialEvent], profile: FinancialProfile,
                     rates: dict, cfg: ReconstructionConfig
                     ) -> tuple[list[FinancialEvent], list[tuple[str, str]], list[str], list[str]]:
    """Apply FX and the confirmed exclusion rules.

    Returns (kept, excluded, unresolved_blank_amount_ids, fx_failure_ids).
    """
    kept: list[FinancialEvent] = []
    excluded: list[tuple[str, str]] = []
    blanks: list[str] = []
    fx_fail: list[str] = []

    for ev in events:
        reason = None

        if ev.direction in NON_CASH_DIRECTIONS or ev.event_type in NON_CASH_EVENT_TYPES:
            reason = "non_cash"
        elif cfg.exclude_unrealized and ev.status == "unrealized":
            reason = "unrealized"
        elif cfg.exclude_cancelled and ev.status == "cancelled":
            reason = "cancelled"
        elif cfg.exclude_failed and ev.status == "failed":
            reason = "failed"
        elif cfg.exclude_pending_credits and ev.status == "pending" and ev.direction == "credit":
            reason = "pending_credit"
        elif cfg.exclude_duplicates and _is_duplicate_row(ev):
            reason = "duplicate"

        if reason:
            ev.excluded_reason = reason
            excluded.append((ev.event_id, reason))
            continue

        if ev.amount is None:
            # Blank amount. NEVER zero. Flag it and drop it from the maths so the
            # caller knows this state is incomplete.
            blanks.append(ev.event_id)
            ev.excluded_reason = "unresolved_blank_amount"
            excluded.append((ev.event_id, "unresolved_blank_amount"))
            continue

        home = convert(ev.amount, ev.currency, profile.home_currency, ev.event_date, rates)
        if home is None:
            fx_fail.append(ev.event_id)
            ev.excluded_reason = "fx_unavailable"
            excluded.append((ev.event_id, "fx_unavailable"))
            continue

        ev.home_amount = home
        kept.append(ev)

    return kept, excluded, blanks, fx_fail


# --------------------------------------------------------------------------
# step 2: known flows (things the dataset states explicitly)
# --------------------------------------------------------------------------


def _effective_date(ev: FinancialEvent, cfg: ReconstructionConfig) -> date:
    if ev.status in ("pending", "scheduled"):
        mode = cfg.unsettled_timing
    else:
        mode = cfg.known_event_timing
    if mode == "settlement_date" and ev.settlement_date:
        return ev.settlement_date
    return ev.event_date


def collect_known_flows(events: list[FinancialEvent], req: Request,
                        cfg: ReconstructionConfig) -> list[Flow]:
    """Real, dataset-stated cash movements that have not yet hit the balance.

    current_available_balance is stated as of request_date, so settled events on
    or before request_date are already inside it. What remains is:
      - unsettled items (pending / scheduled) whose cash has not moved yet
      - anything dated after request_date
    """
    horizon_end = req.request_date + timedelta(days=cfg.horizon_days)
    flows: list[Flow] = []

    for ev in events:
        eff = _effective_date(ev, cfg)
        unsettled = ev.status in ("pending", "scheduled")

        if unsettled:
            if ev.direction == "debit" and not (
                cfg.reserve_pending_debits if ev.status == "pending"
                else cfg.reserve_scheduled_debits
            ):
                continue
            # Reserve it on its effective date, but never before request_date.
            when = max(eff, req.request_date)
        else:
            if eff <= req.request_date:
                continue          # already inside the opening balance
            when = eff

        if when > horizon_end:
            continue
        if when < req.request_date:
            continue
        if when == req.request_date and not cfg.include_flows_on_request_date:
            continue

        flows.append(Flow(
            when=when,
            amount=ev.signed,
            label=ev.description,
            source="pending_reserve" if unsettled else "known_event",
            event_id=ev.event_id,
        ))

    return flows


# --------------------------------------------------------------------------
# step 3: detect recurring commitments
# --------------------------------------------------------------------------


def detect_series(events: list[FinancialEvent], req: Request,
                  cfg: ReconstructionConfig,
                  profile: FinancialProfile | None = None) -> list[RecurringSeries]:
    """Group settled history by (description, category, direction) and keep the
    groups that look like a repeating commitment.

    Reconnaissance finding: `description` is the recurrence key. 164 distinct
    descriptions across the file; a true commitment repeats the same description
    with the same amount on a monthly cadence (rent, loan, subscriptions,
    salary), while discretionary spending repeats the same description with
    varying amounts on an irregular cadence.
    """
    window_start = req.request_date - timedelta(days=cfg.recurrence_lookback_days)
    groups: dict[tuple[str, str, str], list[FinancialEvent]] = defaultdict(list)

    for ev in events:
        salary_anchor = (cfg.income_group_by_category and ev.direction == "credit"
                         and ev.category == "salary" and ev.status == "scheduled")
        if ev.status != "settled" and not salary_anchor:
            continue                      # only completed history defines a pattern
        if ev.event_type in ("investment_purchase", "investment_sale", "refund"):
            continue                      # one-off by nature
        if _is_reversal_row(ev):
            continue
        if ev.event_date < window_start:
            continue
        if ev.event_date > req.request_date and not salary_anchor:
            continue
        if (cfg.income_group_by_category and ev.direction == "credit"
                and ev.category == "salary"):
            groups[("salary stream", "salary", "credit")].append(ev)
        else:
            groups[(ev.description, ev.category, ev.direction)].append(ev)

    series: list[RecurringSeries] = []
    for key, evs in groups.items():
        evs.sort(key=lambda e: e.event_date)
        is_income = evs[0].direction == "credit"
        min_occ = cfg.income_min_occurrences if is_income else cfg.recurrence_min_occurrences
        if len(evs) < min_occ:
            continue

        dates = [e.event_date for e in evs]
        amounts = [e.home_amount for e in evs]

        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        cadence = statistics.median(gaps) if gaps else 30.0
        if cfg.recurrence_min_gap_days is not None and cadence < cfg.recurrence_min_gap_days:
            continue
        if cfg.recurrence_max_gap_days is not None and cadence > cfg.recurrence_max_gap_days:
            continue

        lo, hi = min(amounts), max(amounts)
        tol = cfg.recurrence_amount_tolerance
        is_fixed = (hi - lo) <= tol * max(abs(hi), 1e-9)

        series_filter = cfg.income_series_filter if is_income else cfg.recurrence_series_filter
        if series_filter == "fixed_only" and not is_fixed:
            continue

        if is_income:
            if not cfg.project_income or cfg.income_scope == "scheduled_only":
                continue
            if cfg.income_scope == "fixed_amount" and not is_fixed:
                continue
        else:
            if not cfg.project_expenses:
                continue
            cat, flex = evs[-1].category, evs[-1].flexibility
            if cfg.expense_scope == "fixed_flexibility" and flex != "fixed":
                continue
            if cfg.expense_scope == "protected_categories" and profile is not None:
                if cat not in profile.expense_categories_to_protect:
                    continue
            if cfg.expense_scope == "essential" and cat not in ESSENTIAL_CATEGORIES:
                continue

        amount_mode = cfg.income_amount_mode if is_income else cfg.recurrence_amount_mode
        if amount_mode == "last":
            projected = amounts[-1]
        elif amount_mode == "mean":
            projected = statistics.fmean(amounts)
        elif amount_mode == "trimmed_mean":
            # drop the single highest and lowest observation when there are
            # enough points; robust to one unusual month without chasing the
            # most recent value
            srt = sorted(amounts)
            core = srt[1:-1] if len(srt) >= 4 else srt
            projected = statistics.fmean(core)
        elif amount_mode == "recent_median":
            projected = statistics.median(amounts[-3:])
        else:
            projected = statistics.median(amounts)

        last = evs[-1]
        series.append(RecurringSeries(
            key=key,
            description=last.description,
            category=last.category,
            direction=last.direction,
            event_type=last.event_type,
            flexibility=last.flexibility,
            representative_event_id=last.event_id,
            minimum_allowed_amount=last.minimum_allowed_amount,
            occurrences=dates,
            amounts=amounts,
            is_fixed_amount=is_fixed,
            cadence_days=cadence,
            anchor_day=dates[-1].day,
            projected_amount=projected,
        ))

    series.sort(key=lambda s: s.representative_event_id)
    return series


# --------------------------------------------------------------------------
# step 4: project them forward
# --------------------------------------------------------------------------


def project_series(series: list[RecurringSeries], req: Request,
                   cfg: ReconstructionConfig,
                   blocked: set[tuple[str, str, date]] | None = None,
                   overrides: dict[tuple, float | None] | None = None) -> list[Flow]:
    """Extend each series across the horizon.

    `blocked` suppresses a projected occurrence when the dataset already states a
    real event of the same (category, direction) on that date. This is what stops
    a projected salary colliding with the explicit "Next confirmed salary" row.

    `overrides` implements spending changes: map series key -> None to stop the
    series, or -> amount to reduce every future occurrence to that amount.
    """
    blocked = blocked or set()
    overrides = overrides or {}
    horizon_end = req.request_date + timedelta(days=cfg.horizon_days)
    flows: list[Flow] = []

    for s in series:
        amount = s.projected_amount
        if s.key in overrides:
            override = overrides[s.key]
            if override is None:
                continue                  # stopped
            amount = override             # reduced

        last = s.occurrences[-1]
        step = 1
        while True:
            if cfg.recurrence_cadence == "monthly_day_of_month":
                nxt = add_months(last, step, s.anchor_day)
            elif cfg.recurrence_cadence == "median_gap_days":
                nxt = last + timedelta(days=int(round(s.cadence_days)) * step)
            else:                                   # "hybrid"
                lo, hi = cfg.cadence_monthly_band
                if lo <= s.cadence_days <= hi:
                    nxt = add_months(last, step, s.anchor_day)
                else:
                    nxt = last + timedelta(days=max(1, int(round(s.cadence_days))) * step)
            if nxt > horizon_end:
                break
            step += 1
            if nxt < req.request_date:
                continue
            if nxt == req.request_date and not cfg.include_flows_on_request_date:
                continue
            if (s.category, s.direction, nxt) in blocked:
                continue
            signed = amount if s.direction == "credit" else -amount
            flows.append(Flow(
                when=nxt, amount=signed, label=f"{s.description} (projected)",
                source="projected", event_id=s.representative_event_id, series_key=s.key,
            ))

    return flows


# --------------------------------------------------------------------------
# step 5: timeline
# --------------------------------------------------------------------------


def build_timeline(opening: float, flows: list[Flow], req: Request,
                   cfg: ReconstructionConfig) -> list[TimelineEntry]:
    """Daily balance array over the horizon, inclusive of request_date."""
    end = req.request_date + timedelta(days=cfg.horizon_days)
    if not cfg.horizon_inclusive:
        end -= timedelta(days=1)

    by_day: dict[date, list[Flow]] = defaultdict(list)
    for f in flows:
        if req.request_date <= f.when <= end:
            by_day[f.when].append(f)

    timeline: list[TimelineEntry] = []
    balance = opening
    day = req.request_date
    while day <= end:
        todays = by_day.get(day, [])
        inflow = sum(f.amount for f in todays if f.amount > 0)
        outflow = -sum(f.amount for f in todays if f.amount < 0)
        balance += inflow - outflow
        timeline.append(TimelineEntry(
            day=day, inflow=inflow, outflow=outflow,
            closing_balance=balance, flows=todays,
        ))
        day += timedelta(days=1)
    return timeline


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------


def build_state(ds: Dataset, req: Request,
                cfg: ReconstructionConfig = DEFAULT,
                overrides: dict[tuple, float | None] | None = None,
                evidence=None) -> FinancialState:
    """Reconstruct one user's financial state as of request_date.

    `evidence` is an evidence.Evidence or None. It supplies image-resolved
    amounts and semantic income/expense signals. It never supplies a balance,
    a safe amount, or any other computed figure - only facts about the world.
    """
    profile = ds.profiles[req.user_id]
    raw = [_clone_event(e) for e in ds.events_by_user.get(req.user_id, [])]

    # Evidence step 1: fill blank amounts from image extraction. A blank that
    # evidence could not resolve stays blank and is reported, never zeroed.
    if evidence is not None:
        for e in raw:
            if e.amount is None and e.event_id in evidence.images:
                got = evidence.images[e.event_id]
                if got.amount is not None:
                    e.amount = got.amount
                    e.currency = got.currency or e.currency

    kept, excluded, blanks, fx_fail = normalise_events(raw, profile, ds.rates, cfg)

    known = collect_known_flows(kept, req, cfg)
    blocked = {
        (ds.events_by_id[f.event_id].category, ds.events_by_id[f.event_id].direction, f.when)
        for f in known if f.event_id in ds.events_by_id
    }

    series = detect_series(kept, req, cfg, profile)

    # Evidence step 2: semantic income/expense signals reshape which series are
    # projected and at what amount. Applied as overrides on the deterministic
    # projection, so the arithmetic stays in Python.
    ev_overrides = dict(overrides or {})
    evidence_notes: list[str] = []
    if evidence is not None:
        ev_overrides, evidence_notes = apply_evidence_to_series(
            series, evidence, ev_overrides, req, cfg.variable_income_policy)

    projected = project_series(series, req, cfg, blocked=blocked,
                               overrides=ev_overrides)
    projected.extend(new_recurring_flows(evidence, req, cfg, profile)
                     if evidence is not None else [])

    flows = known + projected
    timeline = build_timeline(profile.current_available_balance, flows, req, cfg)

    return FinancialState(
        request=req,
        profile=profile,
        config=cfg,
        opening_balance=profile.current_available_balance,
        flows=flows,
        timeline=timeline,
        series=series,
        payment_options=ds.options_by_request.get(req.request_id, []),
        excluded=excluded,
        unresolved_blank_amounts=blanks,
        fx_failures=fx_fail,
        evidence_notes=evidence_notes,
    )


# --------------------------------------------------------------------------
# the two numbers calibration cares about
# --------------------------------------------------------------------------


def floor_money(x: float, dp: int = 2) -> float:
    """Round DOWN to `dp` places.

    amount_safe_to_pay must never exceed the true headroom. Rounding to nearest
    can push it up by half a cent, which breaks the minimum-balance guarantee at
    the trough. Flooring is the only safe direction.
    """
    factor = 10 ** dp
    return math.floor(x * factor + 1e-9) / factor


def amount_safe_to_pay(state: FinancialState) -> float:
    """Largest amount payable on request_date without breaching the minimum
    balance at any point in the horizon, capped at requested_amount.

    A single payment today shifts the whole forecast curve down by that amount,
    so the answer is simply the smallest gap between the forecast balance and
    the minimum across the horizon.
    """
    head = state.headroom()
    capped = max(0.0, min(head, state.request.requested_amount))
    # Floor, never round: see floor_money. The cap at requested_amount is exact,
    # so a request for 620.4 still returns 620.4 rather than 620.39.
    if abs(capped - state.request.requested_amount) < 1e-9:
        return capped
    return floor_money(capped, state.config.money_dp)


def earliest_date_for_full_payment(state: FinancialState) -> date | None:
    """First date D in the horizon such that paying the full requested amount on
    D keeps every subsequent day at or above the minimum.

    Computed WITHOUT optional spending changes and WITHOUT regard to which
    payment methods the user accepts, per the problem statement.
    """
    requested = state.request.requested_amount
    for entry in state.timeline:
        if state.headroom(start=entry.day) >= requested:
            return entry.day
    return None


# --------------------------------------------------------------------------
# spending-change helpers (reduce_to is NOT a free choice)
# --------------------------------------------------------------------------


def flexible_series(state: FinancialState) -> list[RecurringSeries]:
    """Recurring series the user is permitted to stop or reduce."""
    p = state.profile
    out = []
    for s in state.series:
        if s.direction != "debit":
            continue
        may_stop = s.is_stoppable and s.category in p.expense_categories_user_is_willing_to_stop
        may_reduce = s.is_reducible and s.category in p.expense_categories_user_is_willing_to_reduce
        if may_stop or may_reduce:
            out.append(s)
    return out


def reduce_to_amount(series: RecurringSeries) -> float | None:
    """CONFIRMED RULE: reduce_to always targets the event's own
    minimum_allowed_amount. Never a computed or chosen value."""
    return series.minimum_allowed_amount


def describe(state: FinancialState, limit: int = 25) -> str:
    """Human-readable dump of one reconstruction, for debugging."""
    r, p = state.request, state.profile
    lines = [
        f"{r.request_id}  {r.user_id}  {r.request_date}  {p.home_currency}",
        f"  opening balance      {state.opening_balance:,.2f}",
        f"  minimum to keep      {p.minimum_balance_to_keep:,.2f}",
        f"  requested amount     {r.requested_amount:,.2f}",
        f"  headroom             {state.headroom():,.2f}  (worst day {state.worst_day()})",
        f"  amount_safe_to_pay   {amount_safe_to_pay(state):,.2f}",
        f"  earliest full        {earliest_date_for_full_payment(state)}",
        f"  series detected      {len(state.series)}",
        f"  flows                {len(state.flows)}",
        f"  unresolved blanks    {state.unresolved_blank_amounts or '-'}",
        "  --- flows in date order ---",
    ]
    for f in sorted(state.flows, key=lambda x: (x.when, x.label))[:limit]:
        lines.append(f"    {f.when}  {f.amount:>16,.2f}  [{f.source}] {f.label}")
    if len(state.flows) > limit:
        lines.append(f"    ... {len(state.flows) - limit} more")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# evidence application (deterministic; evidence supplies facts, not numbers)
# --------------------------------------------------------------------------


def _clone_event(e: FinancialEvent) -> FinancialEvent:
    """Events are mutated during normalisation, so each request works on a copy."""
    return FinancialEvent(
        event_id=e.event_id, user_id=e.user_id, event_type=e.event_type,
        description=e.description, category=e.category, direction=e.direction,
        amount=e.amount, currency=e.currency, event_date=e.event_date,
        settlement_date=e.settlement_date, status=e.status,
        linked_event_id=e.linked_event_id, flexibility=e.flexibility,
        minimum_allowed_amount=e.minimum_allowed_amount,
    )


def _variable_income_amount(s: "RecurringSeries", policy: str) -> float | None:
    """Unconfirmed gig income: never bank the optimistic figure."""
    if policy == "suppress" or not s.amounts:
        return None
    if policy == "median":
        return statistics.median(s.amounts)
    return min(s.amounts)


def apply_evidence_to_series(series: list["RecurringSeries"], evidence,
                             overrides: dict, req: Request,
                             policy: str = "min_observed"
                             ) -> tuple[dict, list[str]]:
    """Translate semantic income/expense signals into projection overrides.

    Closed enum in, numbers out. The classifier can only say which of five
    things is true about a stream; this function decides what that does to the
    forecast, and the forecast is arithmetic.
    """
    notes: list[str] = []

    # --- income -----------------------------------------------------------
    wildcard = next((s for s in evidence.income if s.stream_key == "*"), None)
    for s in series:
        if s.direction != "credit":
            continue
        signal = wildcard or evidence.income_for(s.description)
        if signal is None:
            continue
        cls = signal.classification
        if cls in ("ends", "one_off"):
            # ends    -> the stream stopped
            # one_off -> it was never recurring in the first place
            overrides[s.key] = None
            notes.append(f"income '{s.description}' suppressed ({cls}; "
                         f"{','.join(signal.evidence_ids)})")
        elif cls == "variable_unconfirmed":
            overrides[s.key] = _variable_income_amount(s, policy)
            notes.append(f"income '{s.description}' treated as unconfirmed "
                         f"-> {overrides[s.key]} ({policy})")
        elif cls == "amount_changed_to_X" and signal.new_amount is not None:
            overrides[s.key] = signal.new_amount
            notes.append(f"income '{s.description}' set to {signal.new_amount} "
                         f"({','.join(signal.evidence_ids)})")

    # per-stream classifications that the wildcard did not already handle
    if wildcard is None:
        for s in series:
            if s.direction != "credit" or s.key in overrides:
                continue
            sig = evidence.income_for(s.description)
            if sig and sig.classification in ("ends", "one_off"):
                overrides[s.key] = None
                notes.append(f"income '{s.description}' suppressed "
                             f"({sig.classification})")
            elif sig and sig.classification == "variable_unconfirmed":
                overrides[s.key] = _variable_income_amount(s, policy)
                notes.append(f"income '{s.description}' unconfirmed ({policy})")

    # --- expenses ---------------------------------------------------------
    for sig in evidence.expenses:
        if sig.classification == "expense_increase" and sig.multiplier:
            for s in series:
                if s.direction == "debit" and s.category == sig.target_category:
                    overrides[s.key] = s.projected_amount * sig.multiplier
                    notes.append(f"expense '{s.description}' x{sig.multiplier:.3f} "
                                 f"({','.join(sig.evidence_ids)})")
        elif sig.classification == "expense_cancelled":
            for s in series:
                if s.direction == "debit" and sig.target_category and \
                        s.category == sig.target_category:
                    overrides[s.key] = None
                    notes.append(f"expense '{s.description}' cancelled "
                                 f"({','.join(sig.evidence_ids)})")

    return overrides, notes


def new_recurring_flows(evidence, req: Request, cfg: ReconstructionConfig,
                        profile: FinancialProfile) -> list[Flow]:
    """A message can announce a NEW recurring commitment that has no history.

    Only an explicitly stated amount creates one; nothing is invented.
    """
    out: list[Flow] = []
    horizon_end = req.request_date + timedelta(days=cfg.horizon_days)
    for sig in evidence.expenses:
        if sig.classification != "expense_new_recurring" or not sig.new_amount:
            continue
        start = sig.effective_date or req.request_date
        if start < req.request_date:
            start = req.request_date
        step = 0
        while True:
            when = add_months(start, step, start.day)
            if when > horizon_end:
                break
            if when >= req.request_date:
                out.append(Flow(
                    when=when, amount=-abs(sig.new_amount),
                    label=f"new recurring commitment ({sig.target_category})",
                    source="projected",
                    event_id=",".join(sig.evidence_ids)))
            step += 1
    return out
