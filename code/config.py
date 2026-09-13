"""
config.py - every uncertain modelling assumption, in one place.

NOTHING in state.py hard-codes a rule that we are not certain about. If a rule
came from the problem statement or was verified against the labelled samples it
is implemented directly. If it is an open question (see NOTES.md ambiguities
A1-A10) it lives here as a named parameter so calibrate.py can sweep it.

Read this file top to bottom to understand exactly what the engine assumes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

# --------------------------------------------------------------------------
# Type aliases for the sweepable choices
# --------------------------------------------------------------------------

CadenceMode = Literal["monthly_day_of_month", "median_gap_days", "hybrid"]
AmountMode = Literal["last", "mean", "median", "trimmed_mean", "recent_median"]
UnsettledTiming = Literal["settlement_date", "event_date"]
SeriesFilter = Literal["fixed_only", "fixed_and_variable"]


@dataclass(frozen=True)
class ReconstructionConfig:
    """All tunable assumptions. Frozen so a config can be used as a dict key."""

    # ---------------- horizon (ambiguity A2) ----------------
    # The safety window. Day 0 is request_date itself.
    horizon_days: int = 90
    # Is the last day request_date+90 included in the minimum-balance scan?
    horizon_inclusive: bool = True

    # ---------------- recurrence model (ambiguity A1) ----------------
    # How far back to look for occurrences of a series.
    recurrence_lookback_days: int = 150
    # How many past occurrences before we call something a recurring commitment.
    recurrence_min_occurrences: int = 3
    # Only project series whose amount never changes, or project variable ones too?
    recurrence_series_filter: SeriesFilter = "fixed_and_variable"
    # Income is modelled separately: salary is often variable (unpaid leave,
    # contract end) yet clearly recurring, so it needs its own switches.
    income_series_filter: SeriesFilter = "fixed_and_variable"
    income_amount_mode: AmountMode = "median"
    income_min_occurrences: int = 1
    # How to treat a stream the evidence layer called `variable_unconfirmed`
    # (gig/platform income whose next amount is not confirmed).
    #   suppress     - do not project it at all
    #   min_observed - project the smallest amount ever observed (conservative
    #                  but does not pretend the user has no income)
    #   median       - project the median observed amount
    variable_income_policy: str = "min_observed"
    # Relative tolerance for calling a series "fixed amount". 0.0 = exactly equal.
    recurrence_amount_tolerance: float = 0.0
    # Which amount to use when a variable series IS projected.
    recurrence_amount_mode: AmountMode = "median"
    # How the next occurrence date is computed.
    recurrence_cadence: CadenceMode = "monthly_day_of_month"
    # "hybrid": use monthly-on-day-of-month only when the OBSERVED median gap is
    # actually monthly, and step by the observed gap otherwise. Error analysis
    # showed 18 of 21 informative samples over-predicting headroom, and nearly
    # every user has series recurring every 7, 14, 18 or 21 days. Forcing those
    # onto a monthly cadence projects half their real outflow or less.
    cadence_monthly_band: tuple = (26, 34)
    # A series is only projected if its observed cadence is roughly monthly.
    # Set to None to disable the check.
    recurrence_max_gap_days: int | None = 45
    recurrence_min_gap_days: int | None = 6
    # Which expense series enter the forecast at all.
    #   all                  - every detected recurring series
    #   fixed_flexibility    - only series whose flexibility == "fixed"
    #   protected_categories - only categories in expense_categories_to_protect
    #   essential            - a fixed essential-category list
    expense_scope: str = "all"
    #   all           - every detected recurring credit series
    #   fixed_amount  - only credit series with an unchanging amount
    #   scheduled_only- no projection; use only dataset-stated future income
    income_scope: str = "all"
    # Salary streams are grouped by CATEGORY, not description. The dataset
    # renames a payroll stream as its situation changes ("Payroll credit" ->
    # "Final employer payroll" -> "New employer payroll"; "Prorated first
    # salary" -> "Next confirmed salary"). Grouping by description splits one
    # stream into several short ones, each below the occurrence threshold, and
    # the user's income silently disappears from the forecast.
    income_group_by_category: bool = True

    # Project recurring INCOME (salary) as well as recurring expenses?
    project_income: bool = True
    # Project recurring EXPENSES?
    project_expenses: bool = True

    # ---------------- unsettled items (ambiguity A5 / A10) ----------------
    # On which date does an unsettled debit actually leave the account?
    unsettled_timing: UnsettledTiming = "settlement_date"
    # Same question for ordinary future-dated settled/scheduled events.
    known_event_timing: UnsettledTiming = "settlement_date"
    # Reserve pending debits dated on or before request_date that have not settled.
    reserve_pending_debits: bool = True
    # Reserve scheduled debits (e.g. "Scheduled school fee").
    reserve_scheduled_debits: bool = True

    # ---------------- exclusions (confirmed, but kept switchable) ----------------
    exclude_failed: bool = True            # ambiguity A4: message_69 argues the other way
    exclude_cancelled: bool = True
    exclude_pending_credits: bool = True
    exclude_unrealized: bool = True        # also excludes every non_cash row
    exclude_duplicates: bool = True

    # ---------------- timeline mechanics ----------------
    # Do flows landing exactly on request_date affect the balance we start from?
    include_flows_on_request_date: bool = True
    # Rounding applied to the final safe amount.
    money_dp: int = 2

    def with_(self, **kwargs) -> "ReconstructionConfig":
        """Return a copy with the named fields replaced."""
        return replace(self, **kwargs)

    def label(self) -> str:
        return (
            f"occ>={self.recurrence_min_occurrences},"
            f"{self.recurrence_series_filter},"
            f"{self.recurrence_cadence},"
            f"amt={self.recurrence_amount_mode},"
            f"income={'Y' if self.project_income else 'N'},"
            f"timing={self.unsettled_timing[:4]},"
            f"lookback={self.recurrence_lookback_days},"
            f"H={self.horizon_days}"
        )


# The defaults above are the configuration that maximises TOTAL matched output
# cells across all six scored columns on the 25 labelled samples (100/150),
# selected by calibrate.py. An earlier sweep optimised `amount_safe_to_pay`
# alone; optimising the whole output row is the better objective because five
# of the six columns are recoverable even while the safe-amount magnitude is
# not. See calibration_report.md.
DEFAULT = ReconstructionConfig()

# --------------------------------------------------------------------------
# Facts that are NOT parameters, because they are confirmed
# --------------------------------------------------------------------------

# Statuses that never move cash in the forecast.
NON_CASH_DIRECTIONS = {"non_cash"}

# Event types that are not cash movements out of the current account.
NON_CASH_EVENT_TYPES = {"investment_valuation"}

# Description markers used by the generator for duplicate rows. These rows
# always carry a linked_event_id pointing at the original charge.
DUPLICATE_MARKERS = ("duplicate",)

# Reversal rows are credits that undo an earlier settled debit. Both sides are
# already settled and therefore already inside current_available_balance.
REVERSAL_MARKERS = ("reversal",)

# Only recurring expenses marked flexible may be changed (problem statement).
ESSENTIAL_CATEGORIES = {
    "rent", "housing", "utilities", "groceries", "transport", "education",
    "healthcare", "insurance", "debt_repayment", "family_support",
}

STOPPABLE_FLEXIBILITY = {"stoppable", "reducible_or_stoppable"}
REDUCIBLE_FLEXIBILITY = {"reducible", "reducible_or_stoppable"}
