# Token Usage and Cost Report

Covers the final full-dataset run that produced `output.csv`.

- Provider: **Anthropic**
- Models configured: **claude-sonnet-4-6** (text + vision), **claude-haiku-4-5-20251001** (message classification)
- Temperature: **0** on every call
- Evidence mode for this run: **offline (cached evidence)**
- Requests scored: **250**
- Live model calls: **0**
- Cache hits (calls avoided): **0**
- Input tokens: **0**
- Output tokens: **0**
- Total tokens: **0**
- Average tokens per request: **0.0**
- Estimated total cost: **$0.0000**
- Estimated cost per request: **$0.000000**

## Per-model totals

| provider | model | calls | input | output | total | cost (USD) |
|---|---|---:|---:|---:|---:|---:|
| Anthropic | claude-sonnet-4-6 | 0 | 0 | 0 | 0 | $0.0000 |
| Anthropic | claude-haiku-4-5-20251001 | 0 | 0 | 0 | 0 | $0.0000 |

No live calls were made in this run; the models above are the ones the code is configured to use. Figures are zero because they were measured, not because they were estimated.

## Calls by purpose

| purpose | live calls | model | expected volume with a key |
|---|---:|---|---|
| image_amount_extraction | 0 | claude-sonnet-4-6 | 16 (one per blank-amount event in the whole dataset) |
| message_classification | 0 | claude-haiku-4-5-20251001 | <= 215 (one per message, cached by content hash) |
| explanation | 0 | claude-haiku-4-5-20251001 | <= 250 (one per request, deterministic fallback on failure) |

## Overall totals

| metric | value |
|---|---:|
| live calls | 0 |
| cache hits | 0 |
| input tokens | 0 |
| output tokens | 0 |
| total tokens | 0 |
| estimated cost | $0.0000 |

## Pricing basis

| model | input $/Mtok | output $/Mtok |
|---|---:|---:|
| claude-haiku-4-5-20251001 | 1.00 | 5.00 |
| claude-sonnet-4-6 | 3.00 | 15.00 |

## Notes

The evidence layer is bounded by design: at most one call per blank-amount image (16 in the dataset) and one batched call per user with messages, not one call per request. Every call is keyed into `code/llm_cache.json` by a hash of (model, purpose, payload), so a re-run costs nothing.

When this run reports zero live calls, the pipeline used the checked-in cache: `code/verified_image_amounts.json` for the 16 image extractions and the deterministic archetype matcher for messages. Set `ANTHROPIC_API_KEY` and drop `--offline` to regenerate the evidence with live model calls; the numbers above will then be non-zero.
