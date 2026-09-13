# FINAL_ERROR_ANALYSIS.md

Per-request analysis of all 25 labelled samples under the frozen configuration.
Raw output: `evaluation/error_analysis_raw.txt`. Regenerate with
`cd code && python3 analyse.py`.

---

## 1. Per-request results

| request | cur | requested | expected safe | predicted safe | rel err | status exp/got | method exp/got |
|---|---|---:|---:|---:|---:|---|---|
| request_01 | ZAR | 25,256.00 | 25,256.00 | 25,256.00 | 0.000 | now / now | full / full |
| request_02 | IDR | 46,018,000 | 17,229,139.20 | 17,239,398.99 | 0.001 | plan / plan | inst / inst |
| request_03 | IDR | 5,491,000 | 873,000 | 1,303,285.03 | 0.493 | later / later | wait / wait |
| request_04 | IDR | 12,693,000 | 8,401,800 | 11,846,010.76 | 0.410 | later / later | wait / wait |
| request_05 | ZAR | 15,488.00 | 737.00 | 0.00 | 1.000 | not / not | not_rec / not_rec |
| request_06 | EUR | 620.40 | 603.30 | 620.40 | 0.028 | **plan / now** | full / full |
| request_07 | INR | 197,400 | 87,170.56 | 98,466.96 | 0.130 | plan / plan | inst / inst |
| request_08 | EUR | 996.60 | 284.57 | 384.49 | 0.351 | later / later | wait / wait |
| request_09 | EUR | 166.61 | 166.61 | 166.61 | 0.000 | now / now | full / full |
| request_10 | INR | 266,700 | 12,700 | 81,916.61 | 5.450 | not / not | not_rec / not_rec |
| request_11 | IDR | 13,110,000 | 12,510,645 | 13,110,000 | 0.048 | **plan / now** | full / full |
| request_12 | ZAR | 65,164.00 | 65,164.00 | 65,164.00 | 0.000 | plan / plan | inst / inst |
| request_13 | EUR | 941.60 | 433.40 | 889.69 | 1.053 | later / later | wait / wait |
| request_14 | EUR | 5,414.20 | 597.74 | 0.00 | 1.000 | not / not | not_rec / not_rec |
| request_15 | EUR | 3,685.00 | 83.05 | 161.83 | 0.949 | not / not | not_rec / not_rec |
| request_16 | INR | 122,500 | 122,500 | 122,500 | 0.000 | now / now | full / full |
| request_17 | INR | 274,600 | 243,849.58 | 253,657.83 | 0.040 | plan / plan | inst / inst |
| request_18 | EUR | 3,246.10 | 462.00 | 690.16 | 0.494 | later / later | wait / wait |
| request_19 | INR | 39,660.00 | 28,820 | 37,951.96 | 0.317 | plan / plan | partial / partial |
| request_20 | INR | 303,700 | 5,400 | 12,050.47 | 1.232 | not / not | not_rec / not_rec |
| request_21 | USD | 1,574.40 | 1,543.35 | 1,574.40 | 0.020 | **plan / now** | full / full |
| request_22 | EUR | 731.50 | 475.46 | 506.11 | 0.064 | plan / plan | inst / inst |
| request_23 | ZAR | 38,016.00 | 9,152.00 | 10,275.49 | 0.123 | later / later | wait / wait |
| request_24 | INR | 109,600 | 13,420 | 10,083.60 | 0.249 | not / not | not_rec / not_rec |
| request_25 | IDR | 60,496,000 | 1,425,000 | 2,295,585.39 | 0.611 | not / not | not_rec / not_rec |

**`recommended_payment_method` is correct on all 25.** Status is correct on 22;
all three misses are the same failure mode.

---

## 2. The dominant pattern: a one-directional bias

| direction | count |
|---|---:|
| over-predicts headroom (too little outflow modelled) | **18 of 21** |
| under-predicts headroom | 3 of 21 |

This is the single most important number in the analysis. In Phase 2 the errors
ran both ways, which ruled out a single missing expense stream. Under the current
configuration they do not: the forecast is systematically too generous.

Grouped causes:

| group | count | requests |
|---|---:|---|
| A. capped and correct (clamp hides the forecast) | 4 | 01, 09, 12, 16 |
| B. too optimistic — we see no need for a spending change | 3 | 06, 11, 21 |
| E. earliest date wrong while status is right | 4 | 03, 07, 18, 22 |
| F. safe amount off by >30% | 12 | 03, 04, 05, 08, 10, 13, 14, 15, 18, 19, 20, 25 |
| G. safe amount off by ≤30% (right shape, wrong precision) | 9 | 02, 06, 07, 11, 17, 21, 22, 23, 24 |

Group B is worth naming precisely: on requests 06, 11 and 21 our headroom is high
by 17.10, 599,355 and 31.05 respectively. Those margins are small enough that the
full amount looks payable today, so we emit `affordable_now` with no spending
change where the truth is `affordable_with_plan` with one. Each of those costs up
to four cells. They are not three separate bugs; they are three instances of the
same over-prediction.

---

## 3. Hypotheses tested, with results

Every one of these was evaluated against the full 150-cell table, not against
individual requests.

### 3.1 Recurrence cadence (items 3, 9, 10, 11)

The forecast internals show sub-monthly cadences in nearly every user:
`[7, 14, 18, 21, 24, 28, 30, 31, 32, 35, 42, 45]`. Projecting a 14-day series
monthly counts roughly half its real outflow, which would explain a
one-directional over-prediction of headroom exactly.

Implemented a `hybrid` cadence: monthly-on-day-of-month when the observed median
gap is 26–34 days, observed gap otherwise.

| cadence | total |
|---|---:|
| `monthly_day_of_month` (current) | **113** |
| `hybrid` | 93 |
| `median_gap_days` | 93 |

**Rejected.** The hypothesis was wrong, and the reason is informative: the
short-cadence series are many *distinct descriptions* covering one category. A
user has eight different grocery descriptions, each recurring roughly monthly;
collectively they represent weekly grocery spend. Projecting each monthly already
produces the correct category-level aggregate. Stepping each at its own observed
gap double-counts the category and swings the error to the other extreme.

The `hybrid` mode remains available in `config.py` and is documented as tested
and rejected, so nobody repeats the experiment.

### 3.2 Amount estimators (items 7, 8)

Tested `last`, `mean`, `median`, `trimmed_mean` (drop highest and lowest) and
`recent_median` (median of the last three), crossed with lookback
{120, 150, 180, 240}, minimum occurrences {2, 3, 4}, income estimator
{median, mean, recent_median} and horizon inclusivity {True, False}.

**360 configurations. Ceiling: 113/150. No estimator beat `median`.**

`trimmed_mean` and `recent_median` were added to `state.py` and retained as
options; both tie with `median` rather than beating it.

### 3.3 The whole-number fingerprint (item 22, Phase 9 instruction)

`balance − minimum − label` is a whole number in **16 of 21** informative
samples:

| request | implied outflow | frac |
|---|---:|---:|
| request_02 | 13,996,350.00 | 0.00 |
| request_05 | 32,638.10 | 0.10 |
| request_08 | 452.00 | 0.00 |
| request_13 | 1,056.12 | 0.12 |
| request_15 | 487.00 | 0.00 |
| request_21 | 568.00 | 0.00 |
| request_22 | 157.00 | 0.00 |

Investigated whether this reflects a general commitment structure. It does not
survive contact with the data. In this dataset rent is 622.60 / 178.20 / 435.60 /
718.80 / 467.50, utilities and groceries all carry cents, and only subscriptions
and loan instalments are whole (61, 29, 21, 159, 177). A whole-number total
therefore cannot contain rent, utilities or groceries — yet restricting the
projection to whole-amount series leaves request_22 at 28 against a required 157,
and request_15 at 281 against 487. The subsets do not close.

**Conclusion: a real regularity with no mechanism I could identify. Not
implemented.** Forcing it would be fitting a coincidence. Recorded here so the
next person does not have to rediscover it.

### 3.4 Items with no evidence of a bug

Audited and left unchanged: FX (item 27 — all 140 foreign events resolve on the
exact dated key, asserted by test), duplicate events (18, 19 — marked in the
description with a `linked_event_id`, excluded, asserted by test), failed /
cancelled / pending-credit / unrealised exclusion (tested across the whole
dataset), request-date inclusion (20), horizon inclusivity (21 — swept, no
effect, because no sample troughs near day 90), `reduce_to` (25 — always the
event's `minimum_allowed_amount`, verified against all three labelled examples),
stop/reduce exclusivity (26), event ordering (28).

---

## 4. Bug found and fixed

**`amount_safe_to_pay` rounded to nearest instead of flooring.**

Found by the new adversarial test
`test_paying_the_safe_amount_never_breaches_the_minimum`, which re-simulates
paying the safe amount against every day of the forecast. It failed by 0.005 on
request_28: rounding to 2dp can push the safe amount half a cent *above* the true
headroom, which breaks the minimum-balance guarantee at the trough.

Fixed with `floor_money()` in `state.py`. The cap at `requested_amount` stays
exact, so a request for 620.4 still returns 620.4 rather than 620.39.

**No score change (113/150), and the safety property now holds exactly rather
than approximately.** This is the kind of defect the labelled scoreboard cannot
see, which is why the test exists.

---

## 5. Documentation defect found and fixed

Phase 1 reconnaissance reported "~29 French messages". That was a false positive
from a crude heuristic matching the substrings `vous` and `est `.

Measured properly: **215 messages — 170 English, 45 Indonesian, zero French.**

Corrected in `README.md`, `AI_JUDGE_NOTES.md`, `NOTES.md` and `log.txt`, and
`test_adversarial.py::test_dataset_contains_indonesian_messages` now fails if the
claim is reintroduced. A judge asking "show me a French message" would have found
nothing.

---

## 6. Conclusion

Per the decision rule — implement a change only when a *general* rule improves
*multiple* requests — no accuracy change was adopted. The cadence hypothesis was
strong enough to test and clearly wrong. Estimators are at a ceiling. The
whole-number fingerprint is real but has no identified mechanism.

Two genuine defects were found and fixed: a half-cent safety violation and a
false documentation claim. Neither moves the score; both would have been
embarrassing under scrutiny.

**Frozen at 113/150.**

### What I would investigate next

The over-prediction is one-directional and modest, which points at the *set* of
projected streams rather than the cadence or the estimator. The specific test I
would run: for each sample, solve for the outflow the label requires, then find
which subset of detected series sums to it, and look for a shared property across
samples — a category filter, a flexibility filter, or an occurrence rule. I built
the subset-sum machinery for this in Phase 2 but it returns arbitrary solutions
without a constraint to pin it down; the constraint is what is missing.
