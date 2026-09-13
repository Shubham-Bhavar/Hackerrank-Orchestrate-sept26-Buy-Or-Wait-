# Calibration Report - amount_safe_to_pay

Calibration set: 25 labelled samples in `dataset/sample_requests.csv`.

Configurations evaluated: **864**


4 samples are CAPPED (label == requested_amount), so the clamp hides the forecast and they carry no information about the recurrence model: request_01, request_09, request_12, request_16. The informative set is therefore **21 samples**.


## A. Parameters tested

| Parameter | Values swept |
|---|---|
| `expense_scope` | all, fixed_flexibility, protected_categories, essential |
| `income_scope` | all, fixed_amount, scheduled_only |
| `recurrence_min_occurrences` | 1, 2, 3 |
| `recurrence_amount_mode` | last, mean, median |
| `recurrence_cadence` | monthly_day_of_month, median_gap_days |
| `recurrence_series_filter` | fixed_only, fixed_and_variable |
| `include_flows_on_request_date` | True, False |

Held fixed at their confirmed values: `horizon_days=90`, `exclude_failed`, `exclude_cancelled`, `exclude_pending_credits`, `exclude_unrealized`, `exclude_duplicates`, `unsettled_timing=settlement_date`.


## B. Best configuration

```
essential|all|occ1|median|monthly_day_of_month|fixed_and_variable|onreq=True
```

## C. Accuracy

- exact matches, all 25 samples: **3/25**
- exact matches, informative only: **0/21**
- mean relative error on informative samples: **0.401**

## D. Exact matches

request_09, request_12, request_16

Every one of these is a capped sample. The clamp `min(headroom, requested_amount)` returns the right answer whenever the modelled headroom happens to exceed the requested amount, so these are not evidence that the forecast is correct.


### Best result achieved by ANY configuration

| rank | exact (informative) | mean rel err | configuration |
|---:|---:|---:|---|
| 1 | 0/21 | 0.401 | `essential|all|occ1|median|monthly_day_of_month|fixed_and_variable|onreq=True` |
| 2 | 0/21 | 0.423 | `essential|all|occ2|mean|monthly_day_of_month|fixed_and_variable|onreq=True` |
| 3 | 0/21 | 0.430 | `essential|all|occ2|median|monthly_day_of_month|fixed_and_variable|onreq=True` |
| 4 | 0/21 | 0.437 | `fixed_flexibility|all|occ2|mean|monthly_day_of_month|fixed_and_variable|onreq=True` |
| 5 | 0/21 | 0.444 | `fixed_flexibility|all|occ2|median|monthly_day_of_month|fixed_and_variable|onreq=True` |
| 6 | 0/21 | 0.465 | `essential|all|occ2|mean|monthly_day_of_month|fixed_and_variable|onreq=False` |
| 7 | 0/21 | 0.466 | `fixed_flexibility|all|occ2|mean|monthly_day_of_month|fixed_and_variable|onreq=False` |
| 8 | 0/21 | 0.474 | `essential|all|occ1|median|monthly_day_of_month|fixed_and_variable|onreq=False` |

## E. Requests still incorrect (best configuration)

| request | predicted | expected | abs error | rel error | blanks |
|---|---:|---:|---:|---:|---:|
| request_05 | 15,488.00 | 737.00 | 14,751.00 | 1.818 | 0 |
| request_10 | 266,700.00 | 12,700.00 | 254,000.00 | 1.818 | 0 |
| request_25 | 4,223,203.84 | 1,425,000.00 | 2,798,203.84 | 0.991 | 0 |
| request_13 | 941.60 | 433.40 | 508.20 | 0.739 | 0 |
| request_04 | 12,294,588.35 | 8,401,800.00 | 3,892,788.35 | 0.376 | 0 |
| request_02 | 11,902,373.89 | 17,229,139.20 | 5,326,765.31 | 0.366 | 0 |
| request_15 | 116.52 | 83.05 | 33.47 | 0.335 | 0 |
| request_24 | 18,222.77 | 13,420.00 | 4,802.77 | 0.304 | 0 |
| request_11 | 9,577,063.48 | 12,510,645.00 | 2,933,581.52 | 0.266 | 0 |
| request_18 | 586.45 | 462.00 | 124.45 | 0.237 | 0 |
| request_23 | 7,323.79 | 9,152.00 | 1,828.21 | 0.222 | 0 |
| request_03 | 1,075,507.71 | 873,000.00 | 202,507.71 | 0.208 | 1 |
| request_19 | 33,525.62 | 28,820.00 | 4,705.62 | 0.151 | 1 |
| request_20 | 4,692.03 | 5,400.00 | 707.97 | 0.140 | 1 |
| request_17 | 213,748.94 | 243,849.58 | 30,100.64 | 0.132 | 1 |
| request_14 | 661.92 | 597.74 | 64.18 | 0.102 | 0 |
| request_08 | 262.63 | 284.57 | 21.94 | 0.080 | 0 |
| request_06 | 565.14 | 603.30 | 38.16 | 0.065 | 0 |
| request_22 | 487.08 | 475.46 | 11.62 | 0.024 | 0 |
| request_07 | 89,182.99 | 87,170.56 | 2,012.43 | 0.023 | 0 |
| request_21 | 1,574.40 | 1,543.35 | 31.05 | 0.020 | 0 |
| request_01 | 24,791.10 | 25,256.00 | 464.90 | 0.019 | 0 |

## F. Error patterns

- over-predicts headroom (too little outflow modelled): **14** samples
- under-predicts headroom (too much outflow modelled): **7** samples

Errors run in **both directions** under every configuration tried. A single missing or extra expense stream would bias one way; a two-sided error means the shape of the forecast is wrong, not its magnitude.


### The implied-outflow fingerprint

`balance - minimum - label` is the outflow the ground truth must be modelling at its worst point. It is pure arithmetic on the labels and does not depend on any configuration.

| request | balance | minimum | label | implied outflow | fractional part |
|---|---:|---:|---:|---:|---:|
| request_02 | 60,383,889.20 | 29,158,400.00 | 17,229,139.20 | 13,996,350.00 | +0.00 |
| request_03 | 5,810,300.00 | 2,668,700.00 | 873,000.00 | 2,268,600.00 | +0.00 |
| request_04 | 52,206,950.00 | 30,686,600.00 | 8,401,800.00 | 13,118,550.00 | +0.00 |
| request_05 | 46,475.10 | 13,100.00 | 737.00 | 32,638.10 | +0.10 |
| request_06 | 1,942.40 | 800.00 | 603.30 | 539.10 | +0.10 |
| request_07 | 218,945.56 | 93,000.00 | 87,170.56 | 38,775.00 | +0.00 |
| request_08 | 1,536.57 | 800.00 | 284.57 | 452.00 | -0.00 |
| request_10 | 750,155.00 | 225,400.00 | 12,700.00 | 512,055.00 | +0.00 |
| request_11 | 63,531,795.00 | 34,140,600.00 | 12,510,645.00 | 16,880,550.00 | +0.00 |
| request_13 | 2,789.52 | 1,300.00 | 433.40 | 1,056.12 | +0.12 |
| request_14 | 3,931.74 | 2,200.00 | 597.74 | 1,134.00 | -0.00 |
| request_15 | 1,770.05 | 1,200.00 | 83.05 | 487.00 | -0.00 |
| request_17 | 550,379.58 | 166,100.00 | 243,849.58 | 140,430.00 | -0.00 |
| request_18 | 2,486.00 | 1,400.00 | 462.00 | 624.00 | +0.00 |
| request_19 | 199,545.00 | 92,800.00 | 28,820.00 | 77,925.00 | +0.00 |
| request_20 | 102,609.05 | 64,500.00 | 5,400.00 | 32,709.05 | +0.05 |
| request_21 | 3,911.35 | 1,800.00 | 1,543.35 | 568.00 | +0.00 |
| request_22 | 1,132.46 | 500.00 | 475.46 | 157.00 | +0.00 |
| request_23 | 51,957.90 | 27,000.00 | 9,152.00 | 15,805.90 | -0.10 |
| request_24 | 85,045.00 | 51,000.00 | 13,420.00 | 20,625.00 | +0.00 |
| request_25 | 32,063,050.00 | 23,379,100.00 | 1,425,000.00 | 7,258,950.00 | +0.00 |

**The implied outflow is a whole number in 16 of 21 informative samples.** Most individual events in the dataset carry cents (groceries 62.71, transport 44.85, utilities 134.25), while the fixed commitments are round (rent 622.60, gym 61, music 29, delivery 21, school fee 159, loan 177). A whole-number total is very hard to produce by summing cent-denominated discretionary spend and is exactly what a commitments-only forecast would produce. This is the strongest available clue about what the generator actually modelled.


### Income continuation is encoded in free text, not in a column

The largest single error in the sweep is `request_05`, which is one of only six samples with no message and no image. Its last salary credit is described **"Final employer payroll"**. Every prior month reads "Payroll credit". Nothing in `status`, `event_type` or any other structured column marks the stream as ended, so a deterministic projection keeps paying the user a salary that has stopped.

Suppressing projected income for that one sample moves the prediction from 15,488.00 (capped at the requested amount, 21x the label) to 0.00, against a label of 737.00. Still wrong, but wrong by a normal margin instead of an absurd one.

The full credit-description vocabulary carries the same kind of meaning: `Final employer payroll`, `Previous employer payroll` and `New employer payroll` mark a transition; `Payroll before leave` and `Payroll after returning from leave` bracket a gap; `Seasonal contract payment` and `Peak-season wages` end; `Prorated first salary` and `First-job payroll` start; `Quarterly performance bonus`, `Promotion arrears payment` and `Prize proceeds` are one-offs that must never be projected at all.

| request | last salary description | capped |
|---|---|---|
| request_01 | Prorated first salary | yes |
| request_02 | Payroll credit |  |
| request_03 | August 2019 net salary |  |
| request_04 | Payroll credit |  |
| request_05 | Final employer payroll |  |
| request_06 | Payroll credit |  |
| request_07 | Payroll credit |  |
| request_08 | Payroll credit |  |
| request_09 | Client retainer payment | yes |
| request_10 | Driver platform payout |  |
| request_11 | Monthly sales commission |  |
| request_12 | Temporary assignment pay | yes |
| request_13 | Primary household salary |  |
| request_14 | Payroll after returning from leave |  |
| request_15 | First-job payroll |  |
| request_16 | Payroll credit | yes |
| request_17 | Payroll credit |  |
| request_18 | Payroll credit |  |
| request_19 | Payroll credit |  |
| request_20 | Payroll credit |  |
| request_21 | Payroll credit |  |
| request_22 | Payroll credit |  |
| request_23 | Payroll credit |  |
| request_24 | Payroll credit |  |
| request_25 | International employer payroll |  |

## G. Why this configuration was selected

It is the lowest symmetric relative error across the sweep, not a verified rule. No configuration reproduces a single informative label exactly, so this is a working baseline rather than a calibrated answer.

It is still worth keeping as the Phase 3 starting point, because under it a cluster of samples lands within a few percent of the label (`request_22` +2.4%, `request_07` +2.3%, `request_08` -7.7%, `request_06` -6.3%, `request_17` -12.3%). That cluster says the *shape* of the model is roughly right: opening balance, minus projected essential commitments, plus projected income, take the trough, subtract the minimum. The samples that miss badly are the ones where income does something the structured columns do not describe.


## H. Remaining ambiguity

A1 is **partially resolved**. Across 864 configurations, no purely structural recurrence rule reproduces an informative label exactly, but the error profile identifies why rather than leaving it open.

**Resolved:** the safe amount is the trough of a projected balance curve minus the minimum balance, capped at the requested amount. Under the best configuration a cluster of samples sits within a few percent, which would not happen if the formula were wrong.

**Not resolved, and not resolvable deterministically:** which income and expense streams continue. That information lives in free-text event descriptions and in messages, not in any structured column. This is the boundary between the engine and the agent, and it is exactly where the agent earns its place.

Still genuinely open:

1. The exact expense scope. `essential` beats `all`, but whether the generator used a category list, the `flexibility` column, or something else is untested.
2. Amount selection for variable series. `median` beats `last` and `mean`, which hints the generator used a central estimate rather than the most recent value.
3. Cadence for irregular series. Monthly-on-day-of-month beats median-gap, but several series repeat every 14 or 21 days.
4. Whether the horizon boundary is inclusive. Untested: no sample has its trough near day 90.
5. The five samples whose implied outflow is not a whole number (`request_05`, `request_06`, `request_13`, `request_20`, `request_23`) may share a mechanism the other sixteen do not.

Until income continuation is handled, no downstream column can be trusted, because `affordability_status`, `payment_plan` and `earliest_date_for_full_payment` all derive from the same balance curve.

