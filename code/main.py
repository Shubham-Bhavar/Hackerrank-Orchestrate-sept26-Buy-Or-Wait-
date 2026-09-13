#!/usr/bin/env python3
"""
main.py - entry point. Produces output.csv for every row in requests.csv.

    python3 code/main.py                      # full dataset -> output.csv
    python3 code/main.py --samples            # the 25 labelled samples, scored
    python3 code/main.py --limit 10 --verbose # smoke test

Pipeline per request:

    load -> evidence (LLM/vision, cached) -> state (deterministic)
         -> solve (deterministic) -> explain (LLM, fact-pack bounded)
         -> validate -> repair if needed -> row

No request can crash the run. Any exception produces a conservative, valid row
and is logged to evaluation/errors.log.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

from config import DEFAULT                                      # noqa: E402
from evidence import (                                          # noqa: E402
    build_evidence,
    load_verified_image_amounts,
    resolve_income_conflicts,
)
from explain import explain, render_fallback                    # noqa: E402
from llm import LLMClient, UsageLedger                          # noqa: E402
from solve import conservative_fallback, fmt_safe, solve        # noqa: E402
from state import load_dataset                                  # noqa: E402
from validate import (                                          # noqa: E402
    OUTPUT_COLUMNS,
    ValidationReport,
    repair,
    row_from_decision,
    validate_row,
)


def build_row(ds, req, cfg, llm, verified, rep: ValidationReport,
              errors: list[str]) -> dict:
    try:
        ev = resolve_income_conflicts(
            build_evidence(ds, req, llm, verified, ds.messages_by_user))
        dec = solve(ds, req, cfg, ev)
        text = explain(dec, llm)
    except Exception as exc:                                   # noqa: BLE001
        errors.append(f"{req.request_id}: {type(exc).__name__}: {exc}\n"
                      + traceback.format_exc())
        prof = ds.profiles.get(req.user_id)
        dec = conservative_fallback(
            req, prof.home_currency if prof else "",
            prof.minimum_balance_to_keep if prof else 0.0,
            f"exception: {type(exc).__name__}")
        text = render_fallback(dec)

    row = row_from_decision(dec, text)
    fails = validate_row(row, req, ds, rep)
    if fails:
        row = repair(row, dec, req, ds, fails, rep)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(ROOT, "dataset"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output.csv"))
    ap.add_argument("--samples", action="store_true",
                    help="run the 25 labelled samples and score them")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offline", action="store_true",
                    help="never call the API; use cached/verified evidence only")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    started = time.time()
    ds = load_dataset(args.dataset)
    cfg = DEFAULT

    ledger = UsageLedger()
    llm = LLMClient(os.path.join(HERE, "llm_cache.json"), ledger,
                    offline=args.offline)
    verified = load_verified_image_amounts(
        os.path.join(HERE, "verified_image_amounts.json"))

    mode = "API" if llm.available else "offline (cached evidence)"
    print(f"evidence mode: {mode}; verified image extractions: {len(verified)}")

    requests = ds.sample_requests if args.samples else ds.requests
    if args.limit:
        requests = requests[:args.limit]

    rep = ValidationReport()
    errors: list[str] = []
    rows = [build_row(ds, r, cfg, llm, verified, rep, errors) for r in requests]

    if args.samples:
        score_samples(ds, rows)
    else:
        write_output(args.out, rows)
        print(f"wrote {args.out}: {len(rows)} rows")

    os.makedirs(os.path.join(ROOT, "evaluation"), exist_ok=True)
    ledger.dump(os.path.join(ROOT, "evaluation", "usage.json"))
    ledger.write_report(
        os.path.join(ROOT, "evaluation", "usage_report.md"),
        n_requests=len(rows), mode=mode,
        notes=(
            "The evidence layer is bounded by design: at most one call per "
            "blank-amount image (16 in the dataset) and one batched call per "
            "user with messages, not one call per request. Every call is keyed "
            "into `code/llm_cache.json` by a hash of (model, purpose, payload), "
            "so a re-run costs nothing.\n\n"
            "When this run reports zero live calls, the pipeline used the "
            "checked-in cache: `code/verified_image_amounts.json` for the 16 "
            "image extractions and the deterministic archetype matcher for "
            "messages. Set `ANTHROPIC_API_KEY` and drop `--offline` to "
            "regenerate the evidence with live model calls; the numbers above "
            "will then be non-zero."))
    if llm.available:
        llm.save_cache()
    if errors:
        with open(os.path.join(ROOT, "evaluation", "errors.log"), "w",
                  encoding="utf-8") as fh:
            fh.write("\n".join(errors))
        print(f"!! {len(errors)} request(s) hit an exception; see "
              "evaluation/errors.log")

    print(f"validation: {'PASS' if rep.ok else 'issues found'} - {rep.summary()}")
    if rep.repaired:
        print(f"repaired {len(rep.repaired)} row(s)")
        for r in rep.repaired[:10]:
            print("   ", r)
    totals = ledger.totals()
    print(f"model calls: {totals['live_calls']} live, "
          f"{totals['cache_hits']} cached; tokens {totals['total_tokens']:,}; "
          f"est. cost ${totals['cost_usd']:.4f}")
    print(f"elapsed {time.time() - started:.1f}s")


def write_output(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS,
                           quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    # round-trip check: the file must read back exactly as written
    with open(path, newline="", encoding="utf-8") as fh:
        back = list(csv.DictReader(fh))
    assert len(back) == len(rows), "CSV round-trip changed the row count"
    for a, b in zip(rows, back):
        assert a == b, f"CSV round-trip altered {a['request_id']}"


def score_samples(ds, rows: list[dict]) -> None:
    cols = OUTPUT_COLUMNS[1:-1]
    hits = {c: 0 for c in cols}
    print(f"\n{'request':<13}{'columns':<9}  predicted -> expected")
    for row in rows:
        lab = ds.sample_labels[row["request_id"]]
        marks = ""
        for c in cols:
            ok = row[c].strip() == lab[c].strip()
            hits[c] += ok
            marks += "." if ok else "X"
        print(f"{row['request_id']:<13}{marks:<9}  "
              f"{row['affordability_status']}/{row['recommended_payment_method']}"
              f"  vs  {lab['affordability_status']}/"
              f"{lab['recommended_payment_method']}")
    n = len(rows)
    print()
    total = 0
    for c in cols:
        print(f"  {c:<34}{hits[c]}/{n}")
        total += hits[c]
    print(f"  {'TOTAL CELLS':<34}{total}/{n * len(cols)}")
    with open(os.path.join(ROOT, "evaluation", "sample_scores.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"per_column": hits, "n": n, "total_cells": total,
                   "max_cells": n * len(cols)}, fh, indent=2)


if __name__ == "__main__":
    os.makedirs(os.path.join(ROOT, "evaluation"), exist_ok=True)
    main()
