# Buy or Wait? — HackerRank Orchestrate 2026

An agent that answers one question per request: **can this person afford this, and
if so, how should they pay for it?**

The architecture has a single organising principle:

> **The model interprets ambiguous evidence. Deterministic Python performs the
> financial reconstruction, forecasting, optimisation and safety checks.**

No language model ever produces `amount_safe_to_pay`, `affordability_status`,
`payment_plan`, `earliest_date_for_full_payment`, a balance, an FX conversion, or
a safety verdict. Six of the seven scored columns are exact-match; handing any of
them to a sampler would convert a solvable problem into a coin flip.

---

## The problem

For each of 250 requests: reconstruct the user's financial position as of
`request_date`, forecast it for 90 days, and decide how much can safely be paid
today without the balance ever dropping below `minimum_balance_to_keep`.

The arithmetic is not the hard part. The inputs to the arithmetic are not given
cleanly:

- 16 event amounts are **blank** and live inside a PNG.
- 215 messages amend, cancel, delay or confirm events — 170 English, 45 Indonesian.
- Records are duplicated, failed, pending, cancelled or unrealised.
- 140 events are in a currency other than the user's home currency.
- **Income continuation is encoded in free text**, not in any column.

That last one is the reason this is an agent problem rather than a spreadsheet.

---

## Architecture

### Data flow in one line

`dataset CSVs + PNGs → loader → evidence layer (LLM, closed enums) → state
reconstruction (FX, exclusions, 91-day projection) → solver (headroom, plans,
ranking) → explainer → validator → output.csv`

The evidence layer is the only stage a model touches. Everything downstream of it
is arithmetic.


```
dataset/*.csv  +  media/images/*.png
            │
            ▼
   ┌────────────────────┐
   │ 1. LOADER          │  deterministic joins on user_id, request_id,
   │    state.py        │  related_event_id, (rate_date, from, to)
   └────────────────────┘
            │
            ▼
   ┌────────────────────────────────────────────┐
   │ 2. EVIDENCE LAYER   evidence.py            │   ← the ONLY model use
   │                                            │
   │   income streams  → closed enum            │
   │   messages        → closed enum + figures  │
   │   images          → one amount + label     │
   │                                            │
   │   untrusted content sandbox                │
   └────────────────────────────────────────────┘
            │  structured facts, never numbers that reach the output
            ▼
   ┌────────────────────┐
   │ 3. STATE           │  FX at the exact dated rate; drop failed,
   │    state.py        │  cancelled, duplicates, pending credits,
   │                    │  unrealised; reserve pending debits;
   │                    │  project recurring commitments 90 days
   └────────────────────┘
            │  daily balance array
            ▼
   ┌────────────────────┐
   │ 4. SOLVER          │  headroom → amount_safe_to_pay
   │    solve.py        │  earliest_date_for_full_payment
   │                    │  candidate plans → 6-level ranking
   └────────────────────┘
            │
            ▼
   ┌────────────────────┐
   │ 5. EXPLAINER       │  LLM writes prose from a fact pack.
   │    explain.py      │  Numbers are interpolated, not generated.
   │                    │  Deterministic fallback on any failure.
   └────────────────────┘
            │
            ▼
   ┌────────────────────┐
   │ 6. VALIDATOR       │  schema + invariants + plan re-simulation.
   │    validate.py     │  A failing row is repaired deterministically.
   └────────────────────┘
            │
            ▼
  output.csv  +  evaluation/usage_report.md  +  evaluation/usage.json
```

### Agent workflow, per request

1. Join the profile, events, payment options, messages and images for this user.
2. Resolve any blank amount from its linked image.
3. Classify the user's income streams and messages into a closed enum.
4. Apply the challenge's conflict-resolution hierarchy to the resulting signals.
5. Convert every foreign-currency event at its exact dated rate.
6. Build a 91-day daily balance array (day 0 = `request_date`).
7. Compute the safe amount and the earliest full-payment date.
8. Enumerate candidate plans, filter by eligibility and safety, rank.
9. Write the explanation from the decision.
10. Validate; repair deterministically if the row fails.

---

## The semantic evidence layer

### Income continuation

Phase 2 calibration falsified the obvious approach. Projecting recurring
commitments and taking the trough of the balance curve did not reproduce a
single informative label across 864 configurations. The reason turned out to be
`request_05`, one of only six samples with no message and no image:

> Its last salary credit is described **"Final employer payroll"**. Every prior
> month reads "Payroll credit". Nothing in `status`, `event_type`, `flexibility`
> or any other structured column marks the stream as ended.

A deterministic projection keeps paying a salary that has stopped. Suppressing
it moved that prediction from 15,488.00 to 0.00 against a label of 737.00 —
still wrong, but wrong by a normal margin rather than a 21× one.

The full credit vocabulary carries this meaning. There are 39 distinct credit
descriptions in the dataset, a closed vocabulary, so they are resolved by a
lexicon in `evidence.py` and the model is only consulted for descriptions the
lexicon does not know:

| meaning | examples |
|---|---|
| stream ends | `Final employer payroll`, `Previous employer payroll`, `Seasonal contract payment`, `Peak-season wages` |
| stream starts | `New employer payroll`, `First-job payroll`, `Prorated first salary` |
| gap then resumes | `Payroll before leave`, `Payroll after returning from leave` |
| never project | `Quarterly performance bonus`, `Promotion arrears payment`, `Prize proceeds`, `Investment sale proceeds` |
| gig, unconfirmed | `Driver platform payout`, `Delivery platform payout`, `Weekly app earnings` |

A second consequence: salary streams are grouped by **category**, not
description. The dataset renames a payroll stream as the user's situation
changes, and grouping by description splits one stream into several short ones,
each below the occurrence threshold, so the income silently vanishes from the
forecast. `config.income_group_by_category` controls this and is on by default.

### Messages

215 messages (170 English, 45 Indonesian), batched per user rather than per
request. Classified into a closed
enum covering salary continuation/termination/change, new employer, leave and
return, one-off income, variable income, expense changes, cancellations,
settlements, delays and confirmations.

Conflict resolution follows the challenge hierarchy exactly, in this order:

1. explicit cancellation, settlement or amendment
2. newer record from the same source
3. settled over estimate
4. financially safer interpretation

Messages dated after `request_date` are ignored — they are not knowable yet.

### Images

16 blank amounts, 16 PNGs, a perfect 1:1 match. Extraction is not "find the
biggest number": the **event description names the line item**.

| image | description | document shows | correct |
|---|---|---|---|
| `image_01` | "August 2019 net salary" | Total Earnings 4,780,800 / Net Pay 4,365,000 | **4,365,000** |
| `image_02` | "Outstanding rent balance" | Total 200,000 / Received 100,000 / Balance Due 100,000 | **100,000** |
| `image_10` | grocery invoice | Sub Total 72,045 / Total 79,679.26 / Balance Due 79,679.26 | **79,679.26** |
| `image_14` | "Pharmacy purchase" | handwritten, TOTAL 4543 (line items sum to 4543) | **4,543** |

Every extraction is checked in at `code/verified_image_amounts.json` with the
line item used, the rejected alternatives, and a note explaining the choice.
That file is the cache: the run does not need to call vision again, and each
value is auditable by opening the PNG.

Unreadable or ambiguous evidence is retried once and then marked **unresolved**.
A blank amount is never silently treated as zero.

---

## Deterministic solver

- exact dated FX (`rate_date`, `from`, `to`); all 140 foreign events resolve on
  the first lookup, so no inversion or interpolation is implemented
- **90-day safety horizon**: 91 daily points, day 0 = `request_date` through
  day 90 inclusive; the balance must never fall below `minimum_balance_to_keep`
  on any of them
- excludes `failed`, `cancelled`, `unrealized`, all `non_cash`, pending
  **credits**, and duplicate rows (marked in the description with a
  `linked_event_id`)
- reserves pending and scheduled **debits** on their settlement date
- projects recurring commitments; income overridden by semantic evidence
- `amount_safe_to_pay = clamp(min over horizon of (balance − minimum), 0, requested_amount)`

  A single payment today shifts the whole forecast curve down by that amount, so
  the trough is the binding constraint. This is derived, not fitted.
- `earliest_date_for_full_payment`: first date the full amount clears, computed
  **without** spending changes and **independently** of which methods the user
  accepts

### Payment plans

Installment plans are copied verbatim from a supplied `payment_option_id` —
`number_of_payments`, `first_payment_date`, `payment_amount`, stepping by
`payment_frequency_days`. No plan is invented. A method is eligible only if it
appears in `payment_methods_user_will_consider`; `wait` additionally requires
`full_payment`.

Partial payment enforces all five preconditions as a single guard: status is
`affordable_with_plan`, the request allows partial, the user accepts the method,
`0 < amount_safe_to_pay < requested_amount`, and
`earliest_date_for_full_payment <= desired_completion_date` — then exactly two
payments summing to the requested amount.

Ranking is one tuple sort key in the stated lexicographic order: completes by
the desired date, no spending changes, minimise total paid, start earlier, fewer
payments, lowest `payment_option_id`.

### Spending changes

Only flexible recurring expenses, capped at three. `reduce_to` is **not a chosen
number** — it is always the target event's own `minimum_allowed_amount`, verified
against all three labelled examples. Stop and reduce are mutually exclusive on
the same event.

---

## Validation

`validate.py` checks every row: one row per request, exact column order, valid
enums, `YYYY-MM-DD` dates, `0 <= amount_safe_to_pay <= requested_amount`,
`affordable_now ⇒ earliest = request_date`, `not_affordable ⇒ blank earliest and
plan "none"`, plan format and chronology, payment sums, installment plans
matching a real option, the five partial-payment preconditions, spending-change
validity, and re-simulation of the chosen plan against the balance curve.

A failing row is **repaired deterministically**. The model is never asked to make
output valid.

`output.csv` is written with `QUOTE_MINIMAL`, then read back and asserted equal
row-for-row.

### Formatting conventions

Reverse-engineered from the labelled samples, and they differ by column:

- `amount_safe_to_pay` — rounded to 2dp, trailing zeros **stripped**: `17229139.2`
- `payment_plan` amounts and the `reduce_to` amount — integer if whole, otherwise
  exactly 2dp: `620.40`, not `620.4`
- `payment_plan` is the literal `none` when there is no plan;
  `earliest_date_for_full_payment` is **blank**, never `none`;
  `spending_changes_needed` is the literal `none`
- no thousands separators in any numeric column; they appear only inside
  `decision_explanation`

---

## Safety against prompt injection

Message text and image content are **data, never instructions**. Three defences,
in depth:

1. **Delimiting.** Untrusted text is wrapped in `<untrusted_message>` (images: an explicit untrusted-document instruction) and the system
   prompt states that content inside is to be described, never obeyed.
2. **Closed enums.** Every classifier returns one of a fixed set of labels. An
   injected instruction has no channel through which to express itself.
3. **Post-validation.** Anything outside the enum is coerced to the safe default
   and logged. The model cannot widen its own output space.

Suspected injection attempts are recorded in the evidence log and given no
financial effect. A keyword scan over all 215 messages returned 52 hits, every
one a false positive (ordinary payroll prose containing "instructions",
"approved"). The defences are built because the rules require them and the hidden
set may differ, not because the visible set is adversarial.

---

## How to run

```bash
cd code

# full dataset -> output.csv in the repo root
python3 main.py

# score against the 25 labelled samples
python3 main.py --samples

# no API calls at all; uses the checked-in evidence cache
python3 main.py --offline

# tests
python3 -m pytest ../tests -q

# per-column scoring and the configuration sweep
python3 sweep.py --single
python3 sweep.py
```

Python 3.11, standard library only. No framework, no vector database, no SDK
dependency — the API is called over `urllib` so every request is visible.

To use live model calls, export `ANTHROPIC_API_KEY` and omit `--offline`. No key
is stored anywhere in this repository.

---

## Model and provider

- Provider: **Anthropic**
- Text and vision: `claude-sonnet-4-6`
- Cheap classification path: `claude-haiku-4-5-20251001`
- Temperature 0 everywhere

Calls are bounded by design: at most one per blank-amount image (16 in the whole
dataset) and one batched call per user with messages — not one per request. Every
call is cached by a hash of (model, purpose, payload) in `code/llm_cache.json`,
so a re-run costs nothing. See `evaluation/usage_report.md`.

---

## Results on the labelled samples

| column | exact |
|---|---|
| `recommended_payment_method` | **25/25** |
| `affordability_status` | 22/25 |
| `payment_plan` | 22/25 |
| `spending_changes_needed` | 22/25 |
| `earliest_date_for_full_payment` | 18/25 |
| `amount_safe_to_pay` | 4/25 |
| **total cells** | **113/150** |

Full breakdown, plus the hypotheses tested and rejected: `FINAL_EVALUATION.md` and `FINAL_ERROR_ANALYSIS.md`. Test suite: 120 passed, 1 skipped.

Full breakdown and the hypotheses tested and rejected:  and
. Test suite: 120 passed, 1 skipped.

The shape of that table is the honest summary of this submission: the decision
logic is close to exact, and the forecast that feeds it is close but not precise.

---

## Limitations

1. **`amount_safe_to_pay` is the weak column.** The trough formula is right — a
   cluster of samples lands within a few percent — but the projected expense
   stream is not precise enough for exact match. Under the best configuration
   errors run in both directions, which rules out one missing expense stream.
2. **Income semantics are lexicon-first.** The 39 credit descriptions are a
   closed vocabulary and are resolved deterministically. That is reliable here
   and would not generalise to an unseen vocabulary without the model path.
3. **Image extraction is the least verifiable step.** It is mitigated by the
   volume being small enough (16) to check by hand, which was done, but a hidden
   set would rely on the vision call.
4. **Ambiguous conflicts resolve to the safer reading**, which may be more
   conservative than ground truth.
5. **`request_20` / `image_05` is genuinely ambiguous**: the bill shows 704.05
   due by 06-Feb and 822.05 after. The event settles 09-Feb, so 822.05 was
   chosen. This is documented, not hidden.
6. **Explanation quality is unmeasurable** without labels; it follows the
   six templates observed in the samples.
7. **Ranking rules 3 and 6 cannot be separated** from the labelled data — all
   five sample installment choices are simultaneously the lowest total and the
   lowest option id. Both are implemented in the stated order.

---

## Why numerical decisions are deterministic

Six of the seven scored columns are exact-match or categorical. A model that is
right 95% of the time on a number is wrong on 1 request in 20, silently, with no
way to detect it downstream. A closed-form calculation over reconciled inputs is
either right or has a bug you can find with a test.

So the split is: the model answers *"has this salary stopped?"* and *"which line
on this payslip is the net pay?"* — questions with no closed form. Python answers
*"what is the balance on 14 March?"* — a question that has one.
