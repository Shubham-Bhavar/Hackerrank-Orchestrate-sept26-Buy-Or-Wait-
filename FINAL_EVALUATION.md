# FINAL_EVALUATION.md

Scored on the 25 labelled requests in `dataset/sample_requests.csv`, six scored
columns, 150 cells. Reproduce with `cd code && python3 sweep.py --single`.

---

## Column scores

| column | baseline | final | delta |
|---|---:|---:|---:|
| `recommended_payment_method` | 25/25 | **25/25** | 0 |
| `affordability_status` | 22/25 | **22/25** | 0 |
| `payment_plan` | 22/25 | **22/25** | 0 |
| `spending_changes_needed` | 22/25 | **22/25** | 0 |
| `earliest_date_for_full_payment` | 18/25 | **18/25** | 0 |
| `amount_safe_to_pay` | 4/25 | **4/25** | 0 |
| **TOTAL** | **113/150** | **113/150** | **0** |

Score unchanged, and that is the correct outcome under the stated decision rule.
Every accuracy hypothesis tested either failed against the full table or tied.
Nothing was adopted on the strength of a single request.

## Changes adopted this phase

| change | rationale | score effect |
|---|---|---:|
| `amount_safe_to_pay` floors instead of rounding | rounding to nearest can exceed true headroom by half a cent, breaking the minimum-balance guarantee at the trough | 0 |
| `trimmed_mean` / `recent_median` estimators added | tested as general alternatives; retained as documented options | 0 |
| `hybrid` cadence added, **not enabled** | tested and rejected (93/150); kept visible so the experiment is not repeated | 0 |
| 36 adversarial tests | cover the semantic, hostile and edge-case paths the scoreboard cannot see | 0 |
| French-language claim removed | measured: 215 messages, 170 English, 45 Indonesian, **zero French** | 0 |

## Changes rejected

| hypothesis | result |
|---|---|
| hybrid cadence (monthly when ~30d, observed gap otherwise) | 93/150 — worse, rejected |
| pure `median_gap_days` cadence | 93/150 — worse, rejected |
| 360 combinations of estimator × lookback × occurrences × horizon | ceiling 113/150 — no improvement |
| whole-number commitment rule | real regularity, no identified mechanism, subsets do not close — not forced |

## Request-level results

| request | expected safe | predicted safe | rel err | status | method | plan | earliest | changes |
|---|---:|---:|---:|:-:|:-:|:-:|:-:|:-:|
| request_01 | 25,256.00 | 25,256.00 | 0.000 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_02 | 17,229,139.20 | 17,239,398.99 | 0.001 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_03 | 873,000 | 1,303,285.03 | 0.493 | ✓ | ✓ | ✗ | ✗ | ✓ |
| request_04 | 8,401,800 | 11,846,010.76 | 0.410 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_05 | 737.00 | 0.00 | 1.000 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_06 | 603.30 | 620.40 | 0.028 | ✗ | ✓ | ✓ | ✗ | ✗ |
| request_07 | 87,170.56 | 98,466.96 | 0.130 | ✓ | ✓ | ✓ | ✗ | ✓ |
| request_08 | 284.57 | 384.49 | 0.351 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_09 | 166.61 | 166.61 | 0.000 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_10 | 12,700 | 81,916.61 | 5.450 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_11 | 12,510,645 | 13,110,000 | 0.048 | ✗ | ✓ | ✓ | ✗ | ✗ |
| request_12 | 65,164.00 | 65,164.00 | 0.000 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_13 | 433.40 | 889.69 | 1.053 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_14 | 597.74 | 0.00 | 1.000 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_15 | 83.05 | 161.83 | 0.949 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_16 | 122,500 | 122,500 | 0.000 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_17 | 243,849.58 | 253,657.83 | 0.040 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_18 | 462.00 | 690.16 | 0.494 | ✓ | ✓ | ✗ | ✗ | ✓ |
| request_19 | 28,820 | 37,951.96 | 0.317 | ✓ | ✓ | ✗ | ✓ | ✓ |
| request_20 | 5,400 | 12,050.47 | 1.232 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_21 | 1,543.35 | 1,574.40 | 0.020 | ✗ | ✓ | ✓ | ✗ | ✗ |
| request_22 | 475.46 | 506.11 | 0.064 | ✓ | ✓ | ✓ | ✗ | ✓ |
| request_23 | 9,152.00 | 10,275.49 | 0.123 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_24 | 13,420 | 10,083.60 | 0.249 | ✓ | ✓ | ✓ | ✓ | ✓ |
| request_25 | 1,425,000 | 2,295,585.39 | 0.611 | ✓ | ✓ | ✓ | ✓ | ✓ |

## Test suite

| suite | tests |
|---|---:|
| `test_state.py` — reconstruction | 45 |
| `test_solve.py` — formatting, ranking, plans, validation | 39 |
| `test_adversarial.py` — semantics, hostile input, edge cases | 36 |
| **total** | **120 passed, 1 skipped** |

The skip is conditional: a gig-income description not present among the sample
users.

## Full dataset

| check | result |
|---|---|
| rows written | 250 |
| header exact and in order | PASS |
| one row per request, no duplicates, no missing ids | PASS |
| row order matches `requests.csv` | PASS |
| enum values valid | PASS |
| `0 <= amount_safe_to_pay <= requested_amount` | PASS |
| no newlines in explanations | PASS |
| internal validator | PASS — all rows valid |
| exceptions | 0 |
| CSV round-trip | PASS |
| runtime | 0.7s |

## Honest read

The decision layer is essentially solved: payment method is 25/25, and status,
plan and spending changes are each 22/25. The forecast that feeds them is close
but imprecise, which is why `amount_safe_to_pay` sits at 4/25.

The three status misses (06, 11, 21) are one failure mode, not three: headroom
over-predicted by 17.10, 599,355 and 31.05, just enough to make the full amount
look payable today. Closing them by nudging a threshold would gain up to twelve
cells and would be overfitting to three requests. I did not do it.
