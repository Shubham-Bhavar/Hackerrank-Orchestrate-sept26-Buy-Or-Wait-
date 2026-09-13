"""
solve.py - the deterministic decision engine.

Everything the scorer grades except `decision_explanation` is produced here, by
arithmetic over the timeline that state.py built. No language model is
consulted in this module.

The design follows one observation from the labelled samples: the four
affordability statuses are not four separate code paths. They are the
*consequence* of which candidate plan wins a single lexicographic ranking.

    winner is full payment today, no spending changes  -> affordable_now
    winner is a single future payment                  -> affordable_later  (wait)
    winner is installments / partial / full+changes    -> affordable_with_plan
    no eligible, safe candidate exists                 -> not_affordable

That reproduces the status-by-method cross-tab of the 25 samples exactly,
including the three rows where a spending change makes today's full payment
possible and the status is therefore `affordable_with_plan`, never
`affordable_now`.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta

from config import ReconstructionConfig
from state import (
    Dataset,
    FinancialState,
    PaymentOption,
    Request,
    RecurringSeries,
    amount_safe_to_pay,
    build_state,
    build_timeline,
    earliest_date_for_full_payment,
    flexible_series,
    money,
    reduce_to_amount,
)

MAX_SPENDING_CHANGES = 3


# ==========================================================================
# output formatting - two different conventions, both confirmed from samples
# ==========================================================================


def fmt_safe(x: float) -> str:
    """`amount_safe_to_pay`: 2dp, trailing zeros stripped.

    Confirmed from the samples: 17229139.2, 603.3, 87170.56, 737.
    """
    v = round(float(x) + 0.0, 2)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0")


def fmt_plan(x: float) -> str:
    """`payment_plan` and `reduce_to` amounts: integer if whole, else exactly 2dp.

    Confirmed from the samples: 620.40 (requested was 620.4), 996.60, 23.50,
    but 25256 and 68432 with no decimals.
    """
    v = round(float(x) + 0.0, 2)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}"


# ==========================================================================
# candidate plans
# ==========================================================================


@dataclass
class SpendingChange:
    event_id: str
    action: str          # "stop" | "reduce_to"
    new_amount: float | None
    series_key: tuple
    description: str
    category: str

    def render(self) -> str:
        if self.action == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{fmt_plan(self.new_amount)}"


@dataclass
class Candidate:
    method: str                       # full_payment | partial_payment | installments | wait
    payments: list[tuple[date, float]]
    total_paid: float
    changes: list[SpendingChange] = field(default_factory=list)
    option_id: str = ""
    option_rank: int = 10**9

    @property
    def start(self) -> date:
        return self.payments[0][0]

    @property
    def end(self) -> date:
        return self.payments[-1][0]

    @property
    def n_payments(self) -> int:
        return len(self.payments)

    def completes_by(self, deadline: date) -> bool:
        return self.end <= deadline

    def render_plan(self) -> str:
        return "|".join(f"{d.isoformat()}:{fmt_plan(a)}" for d, a in self.payments)

    def render_changes(self) -> str:
        if not self.changes:
            return "none"
        return "|".join(c.render() for c in self.changes)


def rank_key(c: Candidate, deadline: date) -> tuple:
    """The exact lexicographic ranking from the problem statement.

    1. completes the full request by desired_completion_date
    2. requires no spending changes
    3. minimises total amount paid
    4. starts earlier
    5. fewer payments
    6. lowest payment_option_id
    """
    return (
        0 if c.completes_by(deadline) else 1,
        0 if not c.changes else 1,
        round(c.total_paid, 2),
        c.start,
        c.n_payments,
        c.option_rank,
    )


# ==========================================================================
# safety simulation
# ==========================================================================


def plan_is_safe(state: FinancialState, payments: list[tuple[date, float]],
                 overrides: dict | None = None, ds: Dataset | None = None,
                 cfg: ReconstructionConfig | None = None,
                 evidence=None) -> bool:
    """Re-simulate the whole forecast with the plan's payments applied.

    A candidate is only accepted if it independently passes the minimum-balance
    check. This is the final safety gate and it never trusts the shortcut used
    to generate the candidate.
    """
    sim = state
    if overrides:
        sim = build_state(ds, state.request, cfg, overrides=overrides,
                          evidence=evidence)
    minimum = sim.profile.minimum_balance_to_keep
    horizon_end = sim.request.request_date + timedelta(days=sim.config.horizon_days)
    by_day: dict[date, float] = {}
    for when, amt in payments:
        if when > horizon_end:
            return False
        by_day[when] = by_day.get(when, 0.0) + amt

    balance = sim.opening_balance
    running: dict[date, float] = {}
    cum = 0.0
    for when in sorted(by_day):
        cum += by_day[when]
        running[when] = cum

    outstanding = 0.0
    for entry in sim.timeline:
        if entry.day in by_day:
            outstanding += by_day[entry.day]
        if entry.closing_balance - outstanding < minimum - 1e-6:
            return False
    return True


def headroom_with_changes(ds: Dataset, req: Request, cfg: ReconstructionConfig,
                          overrides: dict, evidence) -> float:
    st = build_state(ds, req, cfg, overrides=overrides, evidence=evidence)
    return st.headroom()


# ==========================================================================
# spending-change search
# ==========================================================================


def change_for(series: RecurringSeries, profile) -> SpendingChange | None:
    """One change per series. Stop and reduce are mutually exclusive.

    reduce_to always equals the event's own minimum_allowed_amount - confirmed
    from all three labelled spending-change rows. Prefer stopping when the user
    allows it, because it frees more cash; fall back to reducing.
    """
    may_stop = (series.is_stoppable
                and series.category in profile.expense_categories_user_is_willing_to_stop)
    may_reduce = (series.is_reducible
                  and series.category in profile.expense_categories_user_is_willing_to_reduce)
    if may_stop:
        return SpendingChange(series.representative_event_id, "stop", None,
                              series.key, series.description, series.category)
    if may_reduce:
        floor = reduce_to_amount(series)
        if floor is None or floor >= series.projected_amount:
            return None
        return SpendingChange(series.representative_event_id, "reduce_to", floor,
                              series.key, series.description, series.category)
    return None


def search_spending_changes(ds: Dataset, req: Request, cfg: ReconstructionConfig,
                            state: FinancialState, evidence, needed: float
                            ) -> list[list[SpendingChange]]:
    """Minimal sets of flexible changes, smallest set first.

    Returns every viable set of size 1..3 in preference order. Callers stop at
    the first that makes their plan safe, which keeps `spending_changes_needed`
    as short as the rules allow.
    """
    options = []
    for s in flexible_series(state):
        ch = change_for(s, state.profile)
        if ch is not None:
            options.append((s, ch))
    if not options:
        return []

    # Largest monthly saving first, so a single change is tried before a pair.
    def saving(pair) -> float:
        s, ch = pair
        return s.projected_amount - (ch.new_amount or 0.0)

    options.sort(key=saving, reverse=True)
    options = options[:8]        # bound the search; 8C3 = 56 combinations

    out: list[list[SpendingChange]] = []
    for size in (1, 2, 3):
        for combo in itertools.combinations(options, size):
            out.append([ch for _, ch in combo])
    return out


def overrides_for(changes: list[SpendingChange]) -> dict:
    return {c.series_key: (None if c.action == "stop" else c.new_amount)
            for c in changes}


# ==========================================================================
# the decision
# ==========================================================================


@dataclass
class Decision:
    request_id: str
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    # facts used by the explanation layer and by validation
    currency: str = ""
    minimum_balance: float = 0.0
    requested_amount: float = 0.0
    desired_completion_date: str = ""
    request_date: str = ""
    n_payments: int = 0
    installment_amount: float | None = None
    option_id: str = ""
    change_descriptions: list[str] = field(default_factory=list)
    unresolved_evidence: list[str] = field(default_factory=list)
    evidence_notes: list[str] = field(default_factory=list)
    fallback: bool = False


def solve(ds: Dataset, req: Request, cfg: ReconstructionConfig, evidence
          ) -> Decision:
    state = build_state(ds, req, cfg, evidence=evidence)
    profile = state.profile
    requested = req.requested_amount
    deadline = req.desired_completion_date
    accepts = profile.payment_methods_user_will_consider

    safe_now = amount_safe_to_pay(state)
    earliest = earliest_date_for_full_payment(state)

    options = sorted(ds.options_by_request.get(req.request_id, []),
                     key=lambda o: o.payment_option_id)
    opt_rank = {o.payment_option_id: i for i, o in enumerate(options)}

    candidates: list[Candidate] = []

    # ---- 1. full payment today, no changes -------------------------------
    if "full_payment" in accepts and state.headroom() >= requested - 1e-6:
        candidates.append(Candidate("full_payment",
                                    [(req.request_date, requested)], requested))

    # ---- 2. wait: one full payment on the earliest safe date -------------
    if "full_payment" in accepts and earliest is not None:
        candidates.append(Candidate("wait", [(earliest, requested)], requested))

    # ---- 3. installments: exactly as supplied ----------------------------
    if "installments" in accepts:
        cap = profile.max_installment_months
        for o in options:
            if o.payment_method != "installments":
                continue
            if cap is not None and o.number_of_payments > cap:
                continue
            sched = o.schedule()
            if plan_is_safe(state, sched):
                candidates.append(Candidate(
                    "installments", sched, o.total_payable_amount,
                    option_id=o.payment_option_id,
                    option_rank=opt_rank[o.payment_option_id]))

    # ---- 4. partial payment: exactly two payments ------------------------
    if ("partial_payment" in accepts and req.allows_partial_payment
            and 0 < safe_now < requested and earliest is not None
            and earliest <= deadline):
        remainder = round(requested - safe_now, 2)
        sched = [(req.request_date, safe_now), (earliest, remainder)]
        if plan_is_safe(state, sched):
            candidates.append(Candidate("partial_payment", sched, requested))

    # ---- 5. full payment today, enabled by flexible spending changes -----
    if "full_payment" in accepts and state.headroom() < requested - 1e-6:
        for combo in search_spending_changes(ds, req, cfg, state, evidence,
                                             requested - state.headroom()):
            ov = overrides_for(combo)
            if headroom_with_changes(ds, req, cfg, ov, evidence) >= requested - 1e-6:
                candidates.append(Candidate(
                    "full_payment", [(req.request_date, requested)], requested,
                    changes=list(combo)))
                break        # minimal set wins; stop at the first that works

    # ---- 6. installments enabled by spending changes ---------------------
    if "installments" in accepts and not any(
            c.method == "installments" for c in candidates):
        for o in options:
            if o.payment_method != "installments":
                continue
            cap = profile.max_installment_months
            if cap is not None and o.number_of_payments > cap:
                continue
            sched = o.schedule()
            done = False
            for combo in search_spending_changes(ds, req, cfg, state, evidence, 0):
                ov = overrides_for(combo)
                if plan_is_safe(state, sched, ov, ds, cfg, evidence):
                    candidates.append(Candidate(
                        "installments", sched, o.total_payable_amount,
                        changes=list(combo), option_id=o.payment_option_id,
                        option_rank=opt_rank[o.payment_option_id]))
                    done = True
                    break
            if done:
                break

    # ---- rank ------------------------------------------------------------
    dec = Decision(
        request_id=req.request_id,
        amount_safe_to_pay=safe_now,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan="none",
        earliest_date_for_full_payment="",
        spending_changes_needed="none",
        currency=profile.home_currency,
        minimum_balance=profile.minimum_balance_to_keep,
        requested_amount=requested,
        desired_completion_date=deadline.isoformat(),
        request_date=req.request_date.isoformat(),
        unresolved_evidence=list(evidence.unresolved) if evidence else [],
        evidence_notes=list(state.evidence_notes),
    )

    if not candidates:
        # not_affordable: earliest_date is blank by the dataset's convention,
        # even when a positive amount could be paid today.
        return dec

    winner = min(candidates, key=lambda c: rank_key(c, deadline))

    dec.payment_plan = winner.render_plan()
    dec.spending_changes_needed = winner.render_changes()
    dec.change_descriptions = [c.description for c in winner.changes]
    dec.n_payments = winner.n_payments
    dec.option_id = winner.option_id
    if winner.method == "installments":
        dec.installment_amount = winner.payments[0][1]

    if winner.method == "wait":
        dec.affordability_status = "affordable_later"
        dec.recommended_payment_method = "wait"
    elif winner.method == "full_payment" and not winner.changes:
        dec.affordability_status = "affordable_now"
        dec.recommended_payment_method = "full_payment"
        dec.amount_safe_to_pay = requested
    else:
        dec.affordability_status = "affordable_with_plan"
        dec.recommended_payment_method = winner.method

    # earliest_date_for_full_payment is measured WITHOUT spending changes and
    # independently of which methods the user accepts.
    if dec.affordability_status == "affordable_now":
        dec.earliest_date_for_full_payment = req.request_date.isoformat()
    elif earliest is not None:
        dec.earliest_date_for_full_payment = earliest.isoformat()
    else:
        dec.earliest_date_for_full_payment = ""

    return dec


def conservative_fallback(req: Request, currency: str = "",
                          minimum: float = 0.0, reason: str = "") -> Decision:
    """Used only when a request raises. Always a valid row, never a crash."""
    return Decision(
        request_id=req.request_id,
        amount_safe_to_pay=0.0,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan="none",
        earliest_date_for_full_payment="",
        spending_changes_needed="none",
        currency=currency,
        minimum_balance=minimum,
        requested_amount=req.requested_amount,
        desired_completion_date=req.desired_completion_date.isoformat(),
        request_date=req.request_date.isoformat(),
        unresolved_evidence=[reason] if reason else [],
        fallback=True,
    )
