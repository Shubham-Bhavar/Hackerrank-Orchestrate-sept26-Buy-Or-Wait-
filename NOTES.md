# NOTES.md — Data Reconnaissance
**HackerRank Orchestrate 2026 · "Buy or Wait?"**
Phase: reconnaissance only. No solver written. Dataset untouched.

Regenerate everything here with:

```bash
python3 explore.py                 # full report
python3 explore.py --only 6,7      # just the convention/invariant sections
python3 explore.py --json --only 1 # machine-readable
```

---

## 1. Dataset Inventory

Source: `https://github.com/interviewstreet/hackerrank-orchestrate-september26` (public repo, cloned at `main`).

| File | Rows | Cols | Bytes |
|---|---:|---:|---:|
| `dataset/requests.csv` | 250 | 8 | 49,671 |
| `dataset/sample_requests.csv` | 25 | 15 | 9,936 |
| `dataset/financial_profiles.csv` | 275 | 10 | 38,046 |
| `dataset/financial_events.csv` | 25,342 | 14 | 2,891,036 |
| `dataset/request_payment_options.csv` | 790 | 9 | 63,454 |
| `dataset/exchange_rates.csv` | 134 | 4 | 3,366 |
| `dataset/messages.csv` | 215 | 7 | 67,865 |
| `dataset/images.csv` | 16 | 4 | 675 |
| `dataset/output.csv` (blank template) | 250 | 8 | 4,843 |
| `dataset/media/images/*.png` | 16 files | — | — |

Non-dataset files in the repo: `problem_statement.md`, `README.md`, `AGENTS.md` (asks AI tools to append to `log.txt` — that file is the `chat_transcript` deliverable), `CLAUDE.md`, `code/main.py` (empty starter), `code/evaluation/`.

**Submission path detail from README (not in problem_statement.md):** the final predictions file must be written to **`output.csv` in the repository root**, not `dataset/output.csv`. `dataset/output.csv` is a read-only template.

---

## 2. Schema of Every File

Types are all read as strings; the "kind" column below is the intended semantic type.

### `requests.csv` — 250 rows, zero blanks in every column
| Column | Kind | Notes |
|---|---|---|
| `request_id` | id | `request_26` … `request_275`. No overlap with sample set. |
| `user_id` | fk → profiles | one request per user; 250 distinct |
| `request_date` | date | 61 distinct values |
| `request_type` | enum(9) | evenly balanced, ~28 each |
| `requested_amount` | decimal | in the user's `home_currency` |
| `desired_completion_date` | date | |
| `allows_partial_payment` | bool | lowercase `true` / `false`; 170 false / 80 true |
| `request_text` | text | natural-language question, restates the amount |

### `sample_requests.csv` — 25 rows
Same 8 input columns **plus the 7 output columns** (`amount_safe_to_pay` … `decision_explanation`). `request_01` … `request_25`, users `user_01` … `user_25`. This is the only labelled data.

### `financial_profiles.csv` — 275 rows, one per user
| Column | Kind | Blank | Notes |
|---|---|---:|---|
| `user_id` | id | 0 | `user_01` … `user_275` |
| `home_currency` | enum(5) | 0 | INR 67, EUR 62, IDR 55, ZAR 51, USD 40 |
| `current_available_balance` | decimal | 0 | balance as of `request_date` |
| `minimum_balance_to_keep` | decimal | 0 | the safety floor |
| `financial_priorities` | pipe-list | 0 | 8 tokens |
| `expense_categories_to_protect` | pipe-list | 0 | 10 tokens |
| `expense_categories_user_is_willing_to_reduce` | pipe-list | 39 (14.2%) | 5 tokens |
| `expense_categories_user_is_willing_to_stop` | pipe-list | 62 (22.5%) | 5 tokens |
| `payment_methods_user_will_consider` | pipe-list | 0 | 3 tokens, 7 combinations |
| `max_installment_months` | int | 119 (43.3%) | 2–12 when present |

### `financial_events.csv` — 25,342 rows
| Column | Kind | Blank | Notes |
|---|---|---:|---|
| `event_id` | id | 0 | `event_01` … `event_25342` |
| `user_id` | fk | 0 | 56–129 events per user, mean 92 |
| `event_type` | enum(8) | 0 | see §3 |
| `description` | text | 0 | 164 distinct; **this is the recurrence key** |
| `category` | enum(22) | 0 | |
| `direction` | enum(3) | 0 | `debit` 23,609 / `credit` 1,723 / `non_cash` 10 |
| `amount` | decimal | **16** | the 16 blanks are the image cases |
| `currency` | enum(5) | 0 | may differ from the user's home currency |
| `event_date` | date | 0 | |
| `settlement_date` | date | 10 | blank only on the 10 `unrealized` valuations |
| `status` | enum(6) | 0 | see §3 |
| `linked_event_id` | fk → event_id | 25,284 (99.8%) | 58 rows use it |
| `flexibility` | enum(4) | 0 | `fixed` / `reducible` / `stoppable` / `reducible_or_stoppable` |
| `minimum_allowed_amount` | decimal | 22,435 (88.5%) | present on reducible events |

### `request_payment_options.csv` — 790 rows
| Column | Kind | Blank | Notes |
|---|---|---:|---|
| `payment_option_id` | id | 0 | `payment_option_01` … `_790` |
| `request_id` | fk | 0 | 275 requests covered (samples + live) |
| `payment_method` | enum(2) | 0 | `full_payment` 275, `installments` 515 |
| `payment_amount` | decimal | 0 | per-instalment amount |
| `number_of_payments` | int | 0 | 1, 2, 3, 4, 6, 15, 18, 21, 24 |
| `first_payment_date` | date | 0 | |
| `payment_frequency_days` | int | 275 | blank **exactly** on the `full_payment` rows; otherwise 28/30/31 |
| `financing_fee` | decimal | 0 | 0 on full-payment rows |
| `total_payable_amount` | decimal | 0 | `= payment_amount × number_of_payments` (≈), includes the fee |

Every request has exactly one `full_payment` option and 1–3 `installments` options (2–4 options total, mean 2.87).

### `exchange_rates.csv` — 134 rows
`rate_date, from_currency, to_currency, rate`. 39 distinct dates spanning 2023-10-15 → 2026-11-15. `from_currency` is only ever USD (88) or EUR (46). Pairs present: EUR→USD, EUR→ZAR, USD→EUR, USD→IDR, USD→INR. Zero duplicate `(date, from, to)` keys.

### `messages.csv` — 215 rows
`message_id, user_id, request_id, related_event_id, sent_at, source_type, message_text`. `sent_at` is ISO-8601 UTC with a `Z`. `source_type` ∈ {employer 126, service_provider 31, financial_service 23, bank 18, merchant 17}. Text is 127–330 chars, mean 251.

### `images.csv` — 16 rows
`image_id, user_id, request_id, related_event_id`. All four columns always populated. Files at `dataset/media/images/<image_id>.png`.

### `output.csv` — blank template
250 rows, `request_id` populated, all seven output columns empty. Row order **exactly matches** `requests.csv`. Column order **exactly matches** the required order.

---

## 3. Important Categorical Values

**`request_type` (9):** `purchase`, `travel`, `education`, `family_transfer`, `debt_repayment`, `investment`, `housing`, `emergency_expense`, `other`.

**`event_type` (8):** `expense` 20,525 · `subscription` 2,488 · `income` 1,696 · `debt_payment` 567 · `investment_purchase` 29 · `refund` 22 · `investment_valuation` 10 · `investment_sale` 5.

**`status` (6):** `settled` 25,148 · `pending` 71 · `scheduled` 70 · `cancelled` 22 · `failed` 21 · `unrealized` 10.

Status × type is highly structured:
- `unrealized` occurs **only** on `investment_valuation` (all 10, all `non_cash`)
- `scheduled` income (47 rows) is the "Next confirmed salary" row
- `pending` splits into 63 expenses and 8 refunds
- `cancelled` (22) and `failed` (21) are all expenses / debt_payments

**`flexibility` (4):** `fixed` 21,138 · `reducible` 2,682 · `stoppable` 1,297 · `reducible_or_stoppable` 225.

**`category` (22):** groceries, transport, dining, salary, utilities, rent, cloud_storage, shopping, streaming, debt_repayment, entertainment, insurance, music_subscription, healthcare, delivery_membership, education, housing, gym, family_support, investment, work_expense, windfall.

**Profile pipe-list token sets:**
- `financial_priorities` (8): emergency_savings, education, retirement_investment, debt_repayment, family_support, travel, healthcare, housing
- `expense_categories_to_protect` (10): rent, groceries, transport, utilities, education, debt_repayment, insurance, healthcare, housing, family_support
- `..._willing_to_reduce` (5): dining, shopping, streaming, entertainment, gym
- `..._willing_to_stop` (5): cloud_storage, streaming, music_subscription, delivery_membership, gym
- `payment_methods_user_will_consider` (3): full_payment 163, installments 156, partial_payment 139

---

## 4. Relationships Between Files

```
financial_profiles (user_id) ──1:N── financial_events (user_id)
        │
        └──1:1── requests / sample_requests (user_id)
                        │
                        ├──1:N── request_payment_options (request_id)
                        ├──0:N── messages (request_id)
                        └──0:1── images (request_id)

financial_events (event_id) ──0:1── images (related_event_id)
financial_events (event_id) ──0:N── messages (related_event_id)
financial_events (event_id) ──0:1── financial_events (linked_event_id)   [self-join]

exchange_rates  keyed on (rate_date, from_currency, to_currency)
```

**Referential integrity: every check passes. Zero orphans.**

| Check | Result |
|---|---|
| `requests.user_id → profiles.user_id` | OK |
| `financial_events.user_id → profiles.user_id` | OK |
| `request_payment_options.request_id → requests(all)` | OK |
| `messages.user_id → profiles.user_id` | OK |
| `messages.request_id → requests(all)` | OK |
| `messages.related_event_id → events.event_id` | OK |
| `images.related_event_id → events.event_id` | OK |
| `events.linked_event_id → events.event_id` | OK |

Coverage across the 250 live requests: **250/250** have payment options, **116/250** have at least one message, **11/250** have an image.

Message attachment levels: 76 user-only, 100 request-level, 39 event-level.

**FX is clean.** 140 events are in a currency other than the user's home currency. Every needed pair exists in `exchange_rates.csv`, and **every one of the 140 has an exact `(event_date, from_currency, to_currency)` row**. No inversion, no cross-rate, no date interpolation required.

---

## 5. Null / Blank Analysis

Only five blanks are semantically meaningful:

| Where | Count | Meaning |
|---|---:|---|
| `financial_events.amount` | 16 | must be read from the linked PNG |
| `financial_events.settlement_date` | 10 | exactly the 10 `unrealized` valuations |
| `financial_events.minimum_allowed_amount` | 22,435 | absent unless the event is reducible |
| `request_payment_options.payment_frequency_days` | 275 | absent exactly on `full_payment` options |
| `profiles.max_installment_months` | 119 | no stated cap |
| `profiles.*_willing_to_reduce / _stop` | 39 / 62 | user is not willing to change anything in that direction |

In the labelled output, blank vs the literal string `none` is **not** interchangeable:

| Output column | blank | literal `none` |
|---|---:|---:|
| `payment_plan` | 0 | **7** |
| `earliest_date_for_full_payment` | **7** | 0 |
| `spending_changes_needed` | 0 | **22** |

---

## 6. Image Findings

16 blank-amount events, 16 images, a perfect 1:1 match. **Zero blank-amount events without an image, and zero images that are not tied to a blank-amount event.** Five belong to the sample set (images 01–05 → requests 03, 16, 17, 19, 20); eleven belong to live requests (03, 16, 17, 19, 20 aside: requests 33, 35, 48, 55, 64, 73, 78, 84, 101, 105, 113).

Sizes range 364×790 to 1628×1366, all RGBA PNG.

### The decisive finding: the event `description` names the line item to extract

| Image | Event | Description | What the image shows | Correct value |
|---|---|---|---|---|
| `image_01` | `event_253` | "August 2019 net salary" | Payslip with **Total Earnings IDR 4,780,800** and **Net Pay IDR 4,365,000** | **4,365,000** (net, not gross) |
| `image_02` | `event_1442` | "Outstanding rent balance" | Rent receipt: Total 2,00,000 / Received 1,00,000 / **Balance Due 1,00,000** | **100,000** (balance due) |
| `image_05` | `event_1786` | "Outstanding telecom bill" | Airtel bill: **Amount due till 06-Feb-2026 = 704.05**, amount due after = 822.05 | see ambiguity A3 |
| `image_14` | `event_9421` | "Pharmacy purchase" | **Handwritten** receipt, six line items, TOTAL 4543.00 | **4,543** (line items 1500+724+796+550+303+670 = 4543 ✓) |

Consequences for design:
- Every one of these documents contains **more than one plausible number**. Gross vs net, total vs balance due, before vs after due date. Naive "find the biggest number" or "find the total" extraction will be wrong on at least three of the five inspected.
- At least one image is **handwritten**, so OCR confidence must be checked and, where line items are present, the sum should be cross-validated against the stated total.
- Images carry **branding and marketing copy** (the Airtel banner). That is untrusted content sitting in the same frame as the number.
- Only 11 live requests need vision, so **the entire vision workload is 11 calls** (16 including samples). These can and should be hand-verified.

---

## 7. Sample-Request Observations (the 25 labelled rows)

### 7.1 Status × method is a near-bijection

| | full_payment | installments | not_recommended | partial_payment | wait |
|---|---:|---:|---:|---:|---:|
| `affordable_now` | **3** | 0 | 0 | 0 | 0 |
| `affordable_with_plan` | **3** | **5** | 0 | **1** | 0 |
| `affordable_later` | 0 | 0 | 0 | 0 | **6** |
| `not_affordable` | 0 | 0 | **7** | 0 | 0 |

All three `affordable_with_plan` + `full_payment` rows (06, 11, 21) are **exactly** the three rows with non-`none` spending changes. So full payment today + a spending change ⇒ `affordable_with_plan`, never `affordable_now`.

### 7.2 Number formatting — two different conventions

`amount_safe_to_pay` is a **plain unpadded number**, 0–2 decimals, no trailing zeros:
`25256`, `17229139.2`, `603.3`, `87170.56`, `83.05`, `737`.
Decimal-place histogram: 0dp ×14, 1dp ×3, 2dp ×8.

`payment_plan` amounts (and the `reduce_to` amount) are **2dp-padded when not whole**:
`620.40` (requested was `620.4`), `996.60`, `941.60`, `3246.10`, `1574.40`, `23.50`.
Whole values carry no decimals: `25256`, `68432`, `13110000`, `28820`.
Decimal-place histogram: 0dp ×11, 2dp ×18.

So: `payment_plan` and `reduce_to` use `round(x, 2)` rendered as integer-if-whole-else-exactly-2dp. `amount_safe_to_pay` uses `round(x, 2)` rendered with trailing zeros stripped.

Thousands separators appear **only inside `decision_explanation`**, never in the numeric columns.

### 7.3 `payment_plan` composition by method

| Method | n payments | Amounts |
|---|---|---|
| `full_payment` | 1 | `request_date : requested_amount` |
| `wait` | 1 | `earliest_date_for_full_payment : requested_amount` |
| `partial_payment` | 2 | `request_date : amount_safe_to_pay` then `earliest_date : remainder` |
| `installments` | 3 (all 5 samples) | copied **verbatim** from a payment option |
| `not_recommended` | — | literal `none` |

All five installment plans matched a supplied option exactly on `number_of_payments`, `first_payment_date` and `payment_amount`, and in every case it was the **lower-numbered, lower-total option** (05 not 07, 19 not 21, 33 not 35, 47 not 49, 61 not 63). Ranking rule 3 (minimise total paid) explains all five, so rules 3 and 6 cannot be distinguished from the samples alone.

Installment plan totals **exceed** `requested_amount` by exactly the `financing_fee` (e.g. request_02: plan total 47,858,720.01 = requested 46,018,000 + fee 1,840,720.01). Only `full_payment`, `wait` and `partial_payment` plans sum to `requested_amount`.

### 7.4 `spending_changes_needed` — the `reduce_to` amount is not free

Three examples, and the rule is exact:

| Row | Change | Target event flexibility | Target `minimum_allowed_amount` |
|---|---|---|---|
| request_06 | `stop:event_476` | `stoppable` | (blank) |
| request_11 | `reduce_to:event_989:665950` | `reducible` | **665950** |
| request_21 | `stop:event_1815` \| `reduce_to:event_1816:23.50` | `stoppable` / `reducible_or_stoppable` | (blank) / **23.5** |

**`reduce_to` always reduces to the event's own `minimum_allowed_amount`.** `stop` targets only `stoppable` events (or the stoppable half of `reducible_or_stoppable`). The referenced `event_id` is a *past* occurrence of a recurring series; the change applies to the forward projection of that series.

### 7.5 `earliest_date_for_full_payment`

- Blank in exactly the 7 `not_affordable` rows and non-blank everywhere else.
- Equals `request_date` in all 3 `affordable_now` rows, and also in request_12 where the method is `installments` (the user does not accept `full_payment`). Confirms the "measures capacity, independent of preference" rule.
- Can fall **after** `desired_completion_date` (requests 06 and 21, both +12d vs +11d) when a spending change makes today's full payment possible.
- Never exceeds +90 days from `request_date` across the 25 samples (max +73d).
- **Lands on the 15th of a month in 12 of 18 non-blank cases.** Salary events settle on the 15th in 109 of 141 sample-user salary rows. The earliest safe date is usually the next payday.

### 7.6 `decision_explanation` templates

85–158 chars, 2–4 sentences, always contains the ISO currency code, thousands-separated numbers, no newlines. Six recurring shapes:

- full payment, no changes — `Pay {CUR} {amt} today. This leaves at least {CUR} {min} available over the next 90 days.`
- full payment, with changes — `{Change in words}, then pay {CUR} {amt} today. This leaves at least {CUR} {min} available.`
- installments — `Use {n} installments of {CUR} {amt}, starting {D Month YYYY}. This leaves at least {CUR} {min} available.`
- wait — `Pay {CUR} {amt} in full on {D Month YYYY}. Paying earlier would take the balance below the {CUR} {min} minimum.` (variant: `Wait until {date}, then pay … Paying sooner would put the {CUR} {min} minimum at risk.`)
- partial — `Pay {CUR} {a} today and the remaining {CUR} {b} on {date}. This completes the full request and keeps the {CUR} {min} minimum protected.`
- not recommended — `Do not make this payment by {date}. None of the available options keeps the {CUR} {min} minimum protected.` (variant: `Do not proceed with the {CUR} {amt} request. Although {CUR} {safe} is available today, the full amount cannot be completed safely within 90 days.`)

The figure quoted after "at least" / "below the" / "keeps the" is **literally `minimum_balance_to_keep`**, verified on every sample. Dates are rendered as `8 August 2025` (no leading zero, full month name).

### 7.7 The forecast must be generated, not read

`current_available_balance − minimum_balance_to_keep − amount_safe_to_pay` is **never zero** and is often large:

| Request | bal − min | safe | residual |
|---|---:|---:|---:|
| request_05 | 33,375.10 | 737.00 | 32,638.10 |
| request_10 | 524,755.00 | 12,700.00 | 512,055.00 |
| request_08 | 736.57 | 284.57 | 452.00 |
| request_22 | 632.46 | 475.46 | 157.00 |

Meanwhile, the **only** future-dated events in the entire file are `Next confirmed salary` rows (47 of them) and a handful of scheduled expenses. `user_01` has exactly one event after `request_date`.

So the 90-day forecast has to be **constructed** by projecting recurring commitments forward from history. The residuals being clean two-decimal values (452.00, 157.00, 624.00, 568.00, 1134.00) is consistent with fixed-amount recurring series (rent, loan instalments, subscriptions) rather than the noisy variable-amount rows.

Recurrence signal, from `user_01`: identical `description` + **identical amount** repeating on a monthly cadence marks a true commitment (`Apartment rent transfer` 5148 ×6 on the 2nd; `Education loan instalment` 3487 ×5 on the 11th; `Delivery service plan` 306.90 ×5 on the 13th; `Music service subscription` 235.40 ×5 on the 11th). Descriptions that repeat with **varying** amounts (`Local market purchase`, `Ride-hailing trip`, `Coffee shop`) are ordinary discretionary spend, not fixed commitments.

---

## 8. Message Findings

Messages are legitimate financial updates, not adversarial text. A keyword scan for injection phrases (`ignore`, `override`, `you must`, `instruction`, `system`, …) returned 52 hits, and **every one inspected was a false positive** — "instructions" appearing in ordinary payroll prose, "approved" in invoice notices. No prompt-injection payload was found in the 215 messages. The guard should still be built, since the problem statement mandates it and the hidden set may differ, but it is not the dominant risk here.

**The dominant message risk is multilingual content.** By crude keyword detection: ~167 English, 45 Indonesian (no French). Example: `message_01` announces an IDR 42,750,000 salary increase effective 2025-08-15 — entirely in Indonesian.

Recurring message archetypes:
- **salary changed** — increase, reduction due to unpaid leave, seasonal contract ended with no renewal (income must be **removed**, not projected)
- **bonus/commission not approved** — do not count it
- **invoice approved** — a specific confirmed credit on a specific date; other invoices explicitly excluded
- **refund initiated but not received** — matches a `pending` refund; ignore it
- **portfolio value increased, no units sold** — matches an `unrealized` valuation; ignore it
- **debit attempt failed, bill still open** — see ambiguity A4
- **image confirmation** — "the receipt has the final INR amount", pointing at a blank-amount event

`message_86` is a compound case: it confirms `event_10521`'s image amount **and** announces a USD 1,296 salary credit for 2026-09-15 for an INR user, noting the rate applies at settlement. One message, two amendments, one FX conversion.

---

## 9. Ambiguities Still Needing Verification

None of these are guessed below. They are flagged for resolution by back-solving against the 25 labelled samples once the solver skeleton exists.

**A1 — How is a recurring series projected forward?**
The cadence must be inferred. Open: monthly-on-the-same-day-of-month vs a fixed day interval; how many past occurrences qualify a series as recurring; whether varying-amount series are projected at all and, if so, at mean/median/last amount. This is the single largest accuracy lever.

**A2 — Is the 90-day window inclusive, and is it measured from `request_date`?**
Samples max out at +73d so the boundary is untested. Also untested: whether `earliest_date_for_full_payment` is searched over the same 90 days or a longer horizon.

**A3 — `image_05` / `event_1786`: 704.05 or 822.05?**
`event_date` is 2026-02-06, matching "Amount due till 06-Feb-2026 = 704.05". But `settlement_date` is 2026-02-09, which falls after the due date, matching "Amount due after 06-Feb-2026 = 822.05". This is `request_20`, a **labelled sample**, so it is back-solvable.

**A4 — `failed` events: ignore or reserve?**
The problem statement says ignore failed transactions. `message_69` says the failed debit's bill "is still outstanding and another debit will be attempted." Rule 4 of the conflict ladder ("financially safer interpretation") argues for reserving it. Untested in the samples.

**A5 — `pending` debits vs `pending` credits.**
The statement says ignore **pending credits**. `pending` debits (63 rows, e.g. `event_102` fuel authorization) and `scheduled` debits are presumably reserved. The README phrase "reserve pending transactions" supports that, but the exact treatment of the settlement lag (debit on `event_date` or on `settlement_date`?) is unverified.

**A6 — Does `max_installment_months` filter the installment options?**
Blank for 43% of users. Whether a user with `max_installment_months = 3` can be offered a 15-payment option is untested — no sample exercised it.

**A7 — Ranking rule 3 vs rule 6.**
All five sample installment choices are simultaneously the lowest total *and* the lowest `payment_option_id`. The rules cannot be separated from the labelled data.

**A8 — When is a spending change allowed at all?**
Sample evidence gives `flexibility` on the event plus `expense_categories_user_is_willing_to_reduce` / `_to_stop` on the profile. Whether both must agree, or `flexibility` alone governs, is unverified. Worth a direct check: do `event_476`/`event_989`/`event_1815`/`event_1816` categories appear in their users' willingness lists?

**A9 — Explanation wording selection.**
Two distinct `not_recommended` phrasings and two `wait` phrasings appear. What selects between them is unknown. Low stakes — the explanation is scored on usefulness and consistency, not exact match.

**A10 — Which `settlement_date` drives the timeline for the "Next confirmed salary" row?**
For salary, `event_date == settlement_date` in 108 of 109 cases, so it rarely matters — but the general rule (cash moves on `settlement_date`) needs confirming from an expense with a lag.

---

## 10. Confirmed Rules — Safe to Implement Now

Each of these is verified against the data, not inferred from the prose.

1. **Output file** goes to `output.csv` in the **repo root**, 250 rows plus header, columns in the template's exact order, row order matching `requests.csv`.
2. `0 ≤ amount_safe_to_pay ≤ requested_amount` holds on all 25 samples.
3. **Status ⇒ method mapping:** `affordable_now` ⇒ `full_payment`; `affordable_later` ⇒ `wait`; `not_affordable` ⇒ `not_recommended`; `affordable_with_plan` ⇒ one of `full_payment` / `installments` / `partial_payment`.
4. `not_affordable` ⇔ `payment_plan` is the literal `none` **and** `earliest_date_for_full_payment` is blank. All other statuses have both populated.
5. `affordable_now` ⇒ `earliest_date_for_full_payment == request_date` **and** `amount_safe_to_pay == requested_amount`.
6. Full payment today that requires a spending change ⇒ status is `affordable_with_plan`, never `affordable_now`.
7. A method is eligible only if it is in `payment_methods_user_will_consider`; `wait` requires `full_payment` to be accepted. Verified on all 25 samples with no exception.
8. `earliest_date_for_full_payment` ignores method preference (request_12: equals `request_date` while the recommendation is `installments`).
9. **Installment plans are copied verbatim from a payment option** — `number_of_payments`, `first_payment_date`, `payment_amount`, stepping by `payment_frequency_days`. Plan total equals `total_payable_amount`, which exceeds `requested_amount` by `financing_fee`.
10. **`reduce_to:<event_id>:<amount>` uses the event's `minimum_allowed_amount`.** `stop:` targets events whose `flexibility` is `stoppable` or `reducible_or_stoppable`.
11. `spending_changes_needed` uses the literal `none`; `payment_plan` uses the literal `none`; `earliest_date_for_full_payment` uses a blank. Never mix these.
12. **Formatting:** `amount_safe_to_pay` = rounded to 2dp, trailing zeros stripped. `payment_plan` amounts and the `reduce_to` amount = rounded to 2dp, rendered as integer if whole, otherwise exactly 2 decimals. No thousands separators in any numeric column.
13. Dates are `YYYY-MM-DD` everywhere except inside `decision_explanation`, which uses `8 August 2025` style.
14. **The number quoted in the explanation after "at least" / "below the" / "keeps the" is `minimum_balance_to_keep` verbatim.**
15. **FX:** look up the exact `(event_date, from_currency, to_currency)` row. All 140 foreign-currency events resolve on the first try. No fallback logic needed.
16. **Blank `amount` ⇒ read the image**, and the **event `description` identifies which line item on the document to take** (net pay, balance due, total).
17. `unrealized` / `non_cash` `investment_valuation` rows are excluded from cash forecasting (10 rows, all with a `linked_event_id` pointing at the purchase).
18. `pending` **credits** (the 8 pending refunds) are excluded.
19. `cancelled` (22) and `failed` (21) rows are excluded from the baseline forecast — subject to ambiguity A4.
20. `flexibility` is an explicit per-event column. It does not have to be inferred from `category`.
21. Recurrence is keyed on `(user_id, description)`. Fixed-amount repeats are commitments; varying-amount repeats are discretionary spend.
22. The vision workload is **11 images for the live set**, small enough to verify by hand.

---

# A. EXACT DATA MODEL

```
FinancialProfile                     one per user_id, 275 total
  home_currency         enum{INR,ZAR,IDR,USD,EUR}
  current_available_balance   decimal, as of request_date
  minimum_balance_to_keep     decimal, the hard floor
  financial_priorities            pipe-list of 8 tokens
  expense_categories_to_protect   pipe-list of 10 tokens
  ..._willing_to_reduce           pipe-list of 5 tokens   (may be empty)
  ..._willing_to_stop             pipe-list of 5 tokens   (may be empty)
  payment_methods_user_will_consider  subset of {full,partial,installments}
  max_installment_months          int or empty

FinancialEvent                       25,342 rows, 56-129 per user
  event_id, user_id
  event_type    enum{expense,subscription,income,debt_payment,
                     investment_purchase,refund,investment_valuation,investment_sale}
  description   164 distinct  <-- recurrence key
  category      22 distinct
  direction     enum{debit,credit,non_cash}
  amount        decimal, BLANK on 16 rows -> read from image
  currency      may differ from home_currency
  event_date, settlement_date
  status        enum{settled,pending,scheduled,cancelled,failed,unrealized}
  linked_event_id   self-FK, 58 rows (valuation->purchase, refund->charge)
  flexibility   enum{fixed,reducible,stoppable,reducible_or_stoppable}
  minimum_allowed_amount   decimal, the floor for reduce_to

Request                              250 live + 25 labelled
  request_id, user_id, request_date, request_type,
  requested_amount, desired_completion_date,
  allows_partial_payment (bool), request_text

PaymentOption                        790 rows, 2-4 per request
  payment_option_id, request_id
  payment_method  enum{full_payment,installments}
  payment_amount, number_of_payments, first_payment_date,
  payment_frequency_days (blank iff full_payment),
  financing_fee, total_payable_amount

Message      215 rows; user-level 76, request-level 100, event-level 39
Image         16 rows; 1:1 with the 16 blank-amount events
ExchangeRate 134 rows; exact key (rate_date, from, to)

OutputRow (the deliverable)
  request_id
  amount_safe_to_pay              2dp, trailing zeros stripped
  affordability_status            enum(4)
  recommended_payment_method      enum(5)
  payment_plan                    "D:A|D:A" or literal "none"
  earliest_date_for_full_payment  YYYY-MM-DD or BLANK
  spending_changes_needed         "stop:e|reduce_to:e:a" or literal "none"
  decision_explanation            2-4 sentences, no newlines
```

# B. EXACT FILE RELATIONSHIPS

- `user_id` joins profile ↔ events ↔ requests ↔ (some) messages. One request per user in this dataset.
- `request_id` joins requests ↔ payment options (always) ↔ messages (100 rows) ↔ images (16 rows).
- `related_event_id` joins messages (39) and images (16) down to a specific event.
- `linked_event_id` is a self-join inside events (58 rows) tying a valuation to its purchase, or a refund to its original charge.
- `(rate_date, from_currency, to_currency)` joins any foreign-currency event to its rate. Exact match, always present, no duplicates.
- Every foreign key resolves. There are no orphans anywhere in the dataset.

# C. CONFIRMED RULES

The 22 numbered items in §10 above. The highest-value five:

1. Status determines method almost bijectively (§10.3–10.6).
2. `reduce_to` amount **is** the event's `minimum_allowed_amount` (§10.10).
3. Installment plans are copied verbatim from a payment option, fee included (§10.9).
4. Two different number-formatting conventions apply to different output columns (§10.12).
5. The 90-day forecast must be **generated** from recurring history; the file contains almost no future-dated events (§7.7).

# D. UNKNOWN / AMBIGUOUS ITEMS

A1–A10 in §9. In priority order for resolution:

| | Item | Why it matters | How to resolve |
|---|---|---|---|
| 1 | A1 recurrence projection model | Drives every number in the output | Back-solve residuals on all 25 samples |
| 2 | A5 pending/scheduled debit timing | Shifts the timeline by days | Back-solve requests 01, 04, 24 |
| 3 | A2 window boundary | Affects `not_affordable` vs `affordable_later` | Construct a synthetic near-boundary case |
| 4 | A8 spending-change eligibility | Affects 3 of 25 sample rows | Cross-check the 4 known target events against profile lists |
| 5 | A3 image_05 due-date reading | One live request, one sample | Back-solve request_20 |
| 6 | A4 failed-event treatment | Conservatism knob | Test both, compare sample accuracy |
| 7 | A6 `max_installment_months` filter | Could invalidate option choices | Check whether any sample user's chosen option exceeds their cap |
| 8 | A7 rule 3 vs rule 6 | Tie-break only | Implement both in stated order; unresolvable from data |
| 9 | A10 settlement vs event date | Small timing effect | Check a lagged expense |
| 10 | A9 explanation wording choice | Soft-scored | Ignore; pick one phrasing per branch |

# E. RECOMMENDED NEXT CODING TASK

**Build the financial-state reconstructor and the 90-day timeline, and calibrate it against the 25 labelled samples. No LLM, no plan selection, no output writing yet.**

Concretely, `code/state.py` should:

1. Join profile + events for one user as of `request_date`.
2. Convert every foreign-currency event with the exact dated rate.
3. Apply the status filters (drop `unrealized`, `cancelled`, pending credits; reserve pending/scheduled debits).
4. Detect recurring series on `(user_id, description)` and project them forward 90 days under a **parameterised** cadence model, so A1 can be tuned rather than guessed.
5. Emit a daily balance array and report `headroom = min(balance[d] − minimum_balance_to_keep)`.

Then `code/calibrate.py` prints, for all 25 samples, predicted `clamp(headroom, 0, requested_amount)` against the labelled `amount_safe_to_pay`, with the error. Sweep the A1/A5 parameters and pick the setting that reproduces the labels.

**The success criterion for this task is not "it runs." It is: `amount_safe_to_pay` matches on a majority of the 25 samples.** Until that holds, every other component is building on sand — and if it does hold, six of the seven output columns follow almost mechanically from the confirmed rules in §10.

Hold the vision, message and explanation work until after this calibration lands. The 16 images and 215 messages are a small, bounded workload that can be added to a correct engine, but they cannot rescue an incorrect one.


---
---

# PHASE 2 — Deterministic State Reconstruction + Calibration

Added: `code/config.py`, `code/state.py`, `code/calibrate.py`, `tests/test_state.py`,
`calibration_report.md`. No vision, no messages, no LLM. 45 unit tests pass.

## 11. Reconstruction Assumptions

Everything the engine assumes is either **confirmed** (implemented directly) or
**uncertain** (a named parameter in `config.py` that `calibrate.py` sweeps).

### 11.1 Confirmed, implemented directly

| # | Assumption | Basis |
|---|---|---|
| R1 | `current_available_balance` is stated as of `request_date`, so settled events on or before that date are already inside it and are not re-applied. | Otherwise the balance would be double-counted; consistent with every sample. |
| R2 | Foreign-currency events convert on the exact `(event_date, from, to)` key. | All 140 foreign events resolve; verified by a test over the real dataset. |
| R3 | Excluded from the forecast: `failed`, `cancelled`, `unrealized`, every `non_cash` row, every `investment_valuation`, and pending **credits**. | Problem statement §decision rules. |
| R4 | Duplicates are the rows described "Possible duplicate card charge" carrying a `linked_event_id`. Reversal rows ("Settled card charge reversal") are already settled on both legs and are not re-applied. | Reconnaissance §duplicates: there are no exact-signature duplicates in the file; duplication is marked in the description. |
| R5 | Pending and scheduled **debits** are reserved on their `settlement_date`, never earlier than `request_date`. | README: "reserve pending transactions". |
| R6 | A blank `amount` is flagged in `state.unresolved_blank_amounts` and excluded from the arithmetic. It is never zero. | Problem statement, explicit. |
| R7 | `reduce_to` targets the event's own `minimum_allowed_amount`. Never a chosen value. | Verified on all three labelled spending-change rows. |
| R8 | `stop:` targets only `stoppable` or `reducible_or_stoppable` series; `reduce_to:` only `reducible` or `reducible_or_stoppable`. | `flexibility` column. |
| R9 | The horizon is 91 days inclusive: `request_date` through `request_date + 90`. | Problem statement. |
| R10 | `amount_safe_to_pay = clamp(min over horizon of (balance - minimum), 0, requested_amount)`. A single payment today shifts the whole curve down by that amount, so the trough is the binding constraint. | Derivation, not data. |
| R11 | A projected occurrence is suppressed when the dataset already states a real event of the same `(category, direction)` on that date. | Stops a projected salary colliding with the explicit "Next confirmed salary" row. |

### 11.2 Uncertain, parameterised

`expense_scope`, `income_scope`, `recurrence_min_occurrences`,
`income_min_occurrences`, `recurrence_series_filter`, `recurrence_amount_mode`,
`income_amount_mode`, `recurrence_cadence`, `recurrence_lookback_days`,
`recurrence_min_gap_days`, `recurrence_max_gap_days`,
`include_flows_on_request_date`, `unsettled_timing`, `known_event_timing`,
`horizon_inclusive`, and the five exclusion switches.

### 11.3 The recurrence model as implemented

1. Group settled history by `(description, category, direction)`. `description` is the recurrence key — 164 distinct values across the file.
2. Drop groups below `recurrence_min_occurrences`, and drop `investment_*`, `refund` and reversal rows, which are one-off by nature.
3. Median inter-occurrence gap becomes the cadence; the day-of-month of the last occurrence becomes the anchor.
4. A group is "fixed amount" when max equals min within `recurrence_amount_tolerance`. The projected amount is the last, mean or median per `recurrence_amount_mode`.
5. Project forward monthly on the anchor day (clamped for short months) or at the median gap, up to the horizon, skipping blocked dates.
6. Spending changes are applied as overrides on the same projection: `None` stops a series, a number reduces every future occurrence.

## 12. Calibration Results

**864 configurations × 25 samples.** Full detail in `calibration_report.md`.

Four samples are **capped** — the label equals `requested_amount`, so the clamp
hides the forecast entirely. They carry no information about the recurrence
model and are excluded from the informative count: `request_01`, `request_09`,
`request_12`, `request_16`.

| | Result |
|---|---|
| Best configuration | `essential` expenses, `all` income, min 1 occurrence, `median` amount, monthly-on-day-of-month, fixed and variable series, flows on `request_date` counted |
| Exact matches, all 25 | **3/25** (all capped) |
| Exact matches, informative | **0/21** |
| Mean symmetric relative error | **0.401** |

### 12.1 What the errors say

The formula is right; the inputs are not. Under the best configuration a cluster
of samples lands within a few percent:

| request | predicted | expected | error |
|---|---:|---:|---:|
| request_22 | 487.08 | 475.46 | +2.4% |
| request_07 | 89,182.99 | 87,170.56 | +2.3% |
| request_08 | 262.63 | 284.57 | −7.7% |
| request_06 | 565.14 | 603.30 | −6.3% |
| request_17 | 213,748.94 | 243,849.58 | −12.3% |

That would not happen if "trough of a projected balance curve, minus the
minimum, capped at the requested amount" were the wrong shape.

Errors run in **both directions**, which rules out a single missing expense
stream. Something about *which* streams continue is wrong, not how many.

### 12.2 The whole-number fingerprint

`balance − minimum − label` is the outflow the ground truth must be modelling at
its worst point. It is pure arithmetic on the labels and depends on no
configuration. **It is a whole number in 16 of the 21 informative samples.**

Most events carry cents (groceries 62.71, transport 44.85, utilities 134.25)
while commitments are round (rent 622.60, gym 61, music 29, delivery 21, school
fee 159, loan 177). A whole-number total is hard to produce by summing
cent-denominated discretionary spend and is what a commitments-weighted forecast
would produce. The five exceptions — `request_05`, `request_06`, `request_13`,
`request_20`, `request_23` — may share a mechanism the other sixteen do not.

### 12.3 The root cause: income continuation is free text

`request_05` is one of only six samples with **no message and no image**, and it
was the worst error in the sweep: predicted 15,488.00 against a label of 737.00.

Its last salary credit is described **"Final employer payroll"**. Every prior
month reads "Payroll credit". Nothing in `status`, `event_type`, `flexibility`
or any other structured column marks the stream as ended. A deterministic
projection keeps paying the user a salary that has stopped.

Suppressing projected income for that one sample moves the prediction from
15,488.00 to 0.00 against a label of 737.00 — still wrong, but wrong by a normal
margin instead of a 21× one.

The credit-description vocabulary carries this meaning throughout:

| Meaning | Descriptions |
|---|---|
| Stream ends | `Final employer payroll`, `Previous employer payroll`, `Seasonal contract payment`, `Peak-season wages` |
| Stream starts | `New employer payroll`, `First-job payroll`, `Prorated first salary` |
| Gap, then resumes | `Payroll before leave`, `Payroll after returning from leave` |
| Never project | `Quarterly performance bonus`, `Promotion arrears payment`, `Prize proceeds`, `Employer expense reimbursement`, `Investment sale proceeds`, `Settled card charge reversal` |
| Variable gig income | `Driver platform payout`, `Delivery platform payout`, `Weekly app earnings`, `Task marketplace payout` |

`request_10` sits in the last row: label 12,700, prediction 266,700. Its message
says the next payout is still pending and weekly earnings can change — gig income
that should not be projected at all.

**This is the boundary between the engine and the agent.** Income continuation is
not recoverable from structured columns. It is exactly the semantic judgement
the agent layer exists to make, which retrospectively validates the Phase 1
architecture: the model decides what is true, the code decides what is safe.

## 13. Revised Ambiguity Status

| Item | Status after Phase 2 |
|---|---|
| A1 recurrence projection | **Partially resolved.** Trough formula confirmed by the near-miss cluster. Scope, amount mode and cadence remain best-guess. |
| A2 horizon boundary | **Still open.** No sample has its trough near day 90. |
| A3 image_05 due-date reading | Still open; needs Phase 3. |
| A4 failed-event treatment | Still open; `exclude_failed=True` throughout. |
| A5 pending/scheduled debit timing | Implemented as `settlement_date`; sweeping it changed nothing material. |
| A6 `max_installment_months` | Not exercised yet; plan selection is Phase 4. |
| A7 ranking rule 3 vs 6 | Unchanged, unresolvable from labels. |
| A8 spending-change eligibility | Implemented as `flexibility` **and** profile willingness list. Untested. |
| A9 explanation wording | Unchanged, low stakes. |
| A10 settlement vs event date | Covered by A5. |
| **A11 (new)** | **Income continuation is encoded in event descriptions and messages, not in any column.** Highest-priority item. |
| **A12 (new)** | Five samples have a non-whole implied outflow. Possibly a shared mechanism. |
