#!/usr/bin/env python3
"""
explore.py - Data reconnaissance for HackerRank Orchestrate 2026 "Buy or Wait?"

READ-ONLY. Does not modify anything in dataset/.

Usage:
    python3 explore.py                 # human-readable report to stdout
    python3 explore.py --json          # machine-readable JSON summary to stdout
    python3 explore.py --dataset PATH  # point at a different dataset dir

Sections:
    1. Inventory          - files, sizes, row counts
    2. Schema             - columns, dtypes, nulls, uniques per file
    3. Vocabularies       - every categorical column's full value set
    4. Relationships      - key joins and referential integrity checks
    5. Blank amounts      - events with missing amount + their linked image
    6. Sample conventions - formatting rules reverse-engineered from sample_requests
    7. Consistency checks - invariants asserted against the 25 solved samples
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

import pandas as pd

# ---------------------------------------------------------------- config

FILES = [
    "requests.csv",
    "sample_requests.csv",
    "financial_profiles.csv",
    "financial_events.csv",
    "request_payment_options.csv",
    "exchange_rates.csv",
    "messages.csv",
    "images.csv",
    "output.csv",
]

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

# Columns treated as categorical vocabularies worth printing in full.
VOCAB_COLUMNS = {
    "requests.csv": ["request_type", "allows_partial_payment"],
    "sample_requests.csv": [
        "request_type",
        "allows_partial_payment",
        "affordability_status",
        "recommended_payment_method",
        "spending_changes_needed",
    ],
    "financial_profiles.csv": [
        "home_currency",
        "financial_priorities",
        "expense_categories_to_protect",
        "expense_categories_user_is_willing_to_reduce",
        "expense_categories_user_is_willing_to_stop",
        "payment_methods_user_will_consider",
        "max_installment_months",
    ],
    "financial_events.csv": [
        "event_type",
        "category",
        "direction",
        "currency",
        "status",
        "flexibility",
    ],
    "request_payment_options.csv": [
        "payment_method",
        "number_of_payments",
        "payment_frequency_days",
    ],
    "exchange_rates.csv": ["from_currency", "to_currency"],
    "messages.csv": ["source_type"],
}

MAX_VOCAB_PRINT = 40


# ---------------------------------------------------------------- helpers

def rule(title: str, char: str = "=") -> None:
    print("\n" + char * 78)
    print(title)
    print(char * 78)


def sub(title: str) -> None:
    print("\n--- " + title + " " + "-" * max(0, 70 - len(title)))


def load_all(dataset_dir: str) -> dict[str, pd.DataFrame]:
    frames = {}
    for name in FILES:
        path = os.path.join(dataset_dir, name)
        if not os.path.exists(path):
            print(f"!! MISSING FILE: {path}", file=sys.stderr)
            continue
        # keep_default_na=False so we can tell "" (blank) from the string "none"
        frames[name] = pd.read_csv(path, dtype=str, keep_default_na=False)
    return frames


def is_blank(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip() == ""


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace("", None), errors="coerce")


def decimals(value: str) -> int | None:
    """Number of digits after the decimal point in a raw CSV amount string."""
    s = str(value).strip()
    if s == "" or s.lower() == "none":
        return None
    if "." not in s:
        return 0
    return len(s.split(".", 1)[1].rstrip())


# ---------------------------------------------------------------- sections

def section_inventory(dataset_dir: str, frames: dict[str, pd.DataFrame]) -> dict:
    rule("1. DATASET INVENTORY")
    out = {}
    print(f"{'file':<32}{'rows':>8}{'cols':>7}{'bytes':>12}")
    print("-" * 60)
    for name, df in frames.items():
        path = os.path.join(dataset_dir, name)
        size = os.path.getsize(path)
        print(f"{name:<32}{len(df):>8}{len(df.columns):>7}{size:>12,}")
        out[name] = {"rows": len(df), "cols": len(df.columns), "bytes": size}

    img_dir = os.path.join(dataset_dir, "media", "images")
    if os.path.isdir(img_dir):
        imgs = sorted(f for f in os.listdir(img_dir) if f.lower().endswith(".png"))
        print(f"\nmedia/images/: {len(imgs)} PNG files")
        print("  " + ", ".join(imgs))
        out["media_images"] = imgs
    return out


def section_schema(frames: dict[str, pd.DataFrame]) -> dict:
    rule("2. SCHEMA / NULL ANALYSIS PER FILE")
    out = {}
    for name, df in frames.items():
        sub(name)
        rows = len(df)
        print(f"{'column':<42}{'blank':>8}{'blank%':>9}{'nuniq':>8}  example")
        print("-" * 96)
        cols = {}
        for col in df.columns:
            blanks = int(is_blank(df[col]).sum())
            nuniq = int(df.loc[~is_blank(df[col]), col].nunique())
            sample = df.loc[~is_blank(df[col]), col]
            ex = str(sample.iloc[0])[:34] if len(sample) else ""
            pct = (blanks / rows * 100) if rows else 0
            print(f"{col:<42}{blanks:>8}{pct:>8.1f}%{nuniq:>8}  {ex}")
            cols[col] = {"blank": blanks, "blank_pct": round(pct, 2), "nunique": nuniq}
        out[name] = {"rows": rows, "columns": cols}
    return out


def section_vocabularies(frames: dict[str, pd.DataFrame]) -> dict:
    rule("3. CATEGORICAL VOCABULARIES (full value sets)")
    out = {}
    for name, cols in VOCAB_COLUMNS.items():
        if name not in frames:
            continue
        df = frames[name]
        sub(name)
        out[name] = {}
        for col in cols:
            if col not in df.columns:
                print(f"  [{col}] -- COLUMN ABSENT")
                continue
            vc = df[col].value_counts(dropna=False)
            print(f"  [{col}]  ({len(vc)} distinct)")
            for val, cnt in vc.head(MAX_VOCAB_PRINT).items():
                shown = "<BLANK>" if str(val).strip() == "" else val
                print(f"      {cnt:>7}  {shown}")
            if len(vc) > MAX_VOCAB_PRINT:
                print(f"      ... {len(vc) - MAX_VOCAB_PRINT} more")
            out[name][col] = {str(k): int(v) for k, v in vc.items()}

            # pipe-delimited multi-value columns: also show the atomic token set
            if df[col].astype(str).str.contains(r"\|").any():
                tokens = Counter()
                for v in df[col]:
                    for t in str(v).split("|"):
                        if t.strip():
                            tokens[t.strip()] += 1
                print(f"      -> atomic tokens ({len(tokens)}): "
                      + ", ".join(f"{k}({v})" for k, v in tokens.most_common()))
                out[name][col + "__tokens"] = dict(tokens)
            print()
    return out


def section_relationships(frames: dict[str, pd.DataFrame]) -> dict:
    rule("4. FILE RELATIONSHIPS / REFERENTIAL INTEGRITY")
    out = {}

    req = frames["requests.csv"]
    smp = frames["sample_requests.csv"]
    prof = frames["financial_profiles.csv"]
    ev = frames["financial_events.csv"]
    opts = frames["request_payment_options.csv"]
    msg = frames["messages.csv"]
    img = frames["images.csv"]
    fx = frames["exchange_rates.csv"]
    tmpl = frames["output.csv"]

    def check(label, left, right, note=""):
        missing = sorted(set(left) - set(right))
        status = "OK" if not missing else f"MISSING {len(missing)}"
        print(f"  {label:<62}{status}")
        if missing:
            print(f"      e.g. {missing[:8]}")
        out[label] = {"missing_count": len(missing), "sample_missing": missing[:8], "note": note}

    sub("id ranges")
    print(f"  sample_requests request_id : {smp.request_id.min()} .. {smp.request_id.max()}  (n={len(smp)})")
    print(f"  requests        request_id : {req.request_id.min()} .. {req.request_id.max()}  (n={len(req)})")
    print(f"  overlap between the two    : {len(set(req.request_id) & set(smp.request_id))}")
    print(f"  profiles        user_id    : {prof.user_id.min()} .. {prof.user_id.max()}  (n={len(prof)})")
    print(f"  output template request_id : {len(tmpl)} rows, matches requests.csv order: "
          f"{list(tmpl.request_id) == list(req.request_id)}")
    print(f"  output template columns == required order: {list(tmpl.columns) == OUTPUT_COLUMNS}")

    sub("foreign keys")
    all_req = pd.concat([req[["request_id", "user_id"]], smp[["request_id", "user_id"]]])
    check("requests.user_id -> financial_profiles.user_id", all_req.user_id, prof.user_id)
    check("financial_events.user_id -> financial_profiles.user_id", ev.user_id, prof.user_id)
    check("request_payment_options.request_id -> requests(all)", opts.request_id, all_req.request_id)
    check("messages.user_id -> financial_profiles.user_id",
          msg.loc[~is_blank(msg.user_id), "user_id"], prof.user_id)
    check("messages.request_id -> requests(all)",
          msg.loc[~is_blank(msg.request_id), "request_id"], all_req.request_id)
    check("messages.related_event_id -> financial_events.event_id",
          msg.loc[~is_blank(msg.related_event_id), "related_event_id"], ev.event_id)
    check("images.related_event_id -> financial_events.event_id",
          img.loc[~is_blank(img.related_event_id), "related_event_id"], ev.event_id)
    check("financial_events.linked_event_id -> financial_events.event_id",
          ev.loc[~is_blank(ev.linked_event_id), "linked_event_id"], ev.event_id)

    sub("coverage: does every request have options / events / messages / images?")
    req_ids = set(req.request_id)
    print(f"  requests with >=1 payment option : {len(req_ids & set(opts.request_id))} / {len(req_ids)}")
    print(f"  requests with >=1 message        : {len(req_ids & set(msg.request_id))} / {len(req_ids)}")
    print(f"  requests with >=1 image          : {len(req_ids & set(img.request_id))} / {len(req_ids)}")
    per_req = opts.groupby("request_id").size()
    print(f"  payment options per request      : min={per_req.min()} max={per_req.max()} "
          f"mean={per_req.mean():.2f}")
    per_user = ev.groupby("user_id").size()
    print(f"  financial events per user        : min={per_user.min()} max={per_user.max()} "
          f"mean={per_user.mean():.1f}")

    sub("messages: attachment level")
    lvl = Counter()
    for _, r in msg.iterrows():
        has_r = str(r.request_id).strip() != ""
        has_e = str(r.related_event_id).strip() != ""
        lvl[("event" if has_e else "request" if has_r else "user-only")] += 1
    for k, v in lvl.items():
        print(f"  {k:<14}{v}")

    sub("exchange rates")
    print(f"  pairs: {sorted(set(zip(fx.from_currency, fx.to_currency)))}")
    print(f"  date range: {fx.rate_date.min()} .. {fx.rate_date.max()}  ({fx.rate_date.nunique()} distinct dates)")
    dup = fx.duplicated(subset=["rate_date", "from_currency", "to_currency"]).sum()
    print(f"  duplicate (date,from,to) keys: {dup}")
    home = set(prof.home_currency)
    ev_cur = set(ev.currency) - {""}
    print(f"  home currencies: {sorted(home)}")
    print(f"  event currencies: {sorted(ev_cur)}")
    print(f"  foreign-currency events (currency != user's home): ", end="")
    merged = ev.merge(prof[["user_id", "home_currency"]], on="user_id", how="left")
    foreign = merged[(merged.currency != "") & (merged.currency != merged.home_currency)]
    print(len(foreign))
    if len(foreign):
        print("    pairs needed: "
              + str(sorted(set(zip(foreign.currency, foreign.home_currency)))))
        have = set(zip(fx.from_currency, fx.to_currency))
        need = set(zip(foreign.currency, foreign.home_currency))
        print(f"    pairs present in exchange_rates.csv: {sorted(need & have)}")
        print(f"    pairs MISSING (may need inversion/cross-rate): {sorted(need - have)}")
        # does every foreign event date have an exact rate row?
        fxkeys = set(zip(fx.rate_date, fx.from_currency, fx.to_currency))
        miss = [(r.event_date, r.currency, r.home_currency) for _, r in foreign.iterrows()
                if (r.event_date, r.currency, r.home_currency) not in fxkeys]
        print(f"    foreign events with NO exact (event_date,from,to) rate row: {len(miss)}")
        if miss:
            print(f"      e.g. {miss[:5]}")
    out["foreign_event_count"] = len(foreign)
    return out


def section_blank_amounts(frames: dict[str, pd.DataFrame], dataset_dir: str) -> dict:
    rule("5. BLANK EVENT AMOUNTS AND THEIR LINKED IMAGES")
    ev = frames["financial_events.csv"]
    img = frames["images.csv"]

    blanks = ev[is_blank(ev.amount)]
    print(f"  financial_events rows with blank amount: {len(blanks)}")
    print(f"  images.csv rows: {len(img)}")
    print(f"  images with a related_event_id: {int((~is_blank(img.related_event_id)).sum())}")

    sub("every blank-amount event, joined to its image")
    merged = blanks.merge(
        img, left_on="event_id", right_on="related_event_id", how="left",
        suffixes=("", "_img"))
    cols = ["event_id", "user_id", "event_type", "category", "direction", "currency",
            "event_date", "settlement_date", "status", "flexibility", "image_id", "description"]
    for _, r in merged.iterrows():
        print("  " + " | ".join(f"{c}={r.get(c, '')}" for c in cols))
    unmatched = merged[is_blank(merged.image_id.fillna(""))]
    print(f"\n  blank-amount events with NO image: {len(unmatched)}")

    sub("images NOT linked to a blank-amount event (context-only images)")
    blank_ids = set(blanks.event_id)
    for _, r in img.iterrows():
        if str(r.related_event_id).strip() not in blank_ids:
            ev_row = ev[ev.event_id == r.related_event_id]
            desc = ev_row.description.iloc[0] if len(ev_row) else "(no event)"
            amt = ev_row.amount.iloc[0] if len(ev_row) else ""
            print(f"  {r.image_id} | user={r.user_id} | request={r.request_id} | "
                  f"event={r.related_event_id} | event_amount={amt!r} | {desc}")

    sub("full images.csv dump")
    print(img.to_string(index=False))

    return {"blank_amount_events": len(blanks),
            "blank_event_ids": list(blanks.event_id),
            "images": len(img)}


def section_sample_conventions(frames: dict[str, pd.DataFrame]) -> dict:
    rule("6. OUTPUT CONVENTIONS REVERSE-ENGINEERED FROM sample_requests.csv")
    smp = frames["sample_requests.csv"]
    out = {}

    sub("full dump of all 25 solved samples (one block per request)")
    for _, r in smp.iterrows():
        print(f"\n### {r.request_id}  user={r.user_id}  date={r.request_date}  type={r.request_type}")
        print(f"    requested_amount        = {r.requested_amount}")
        print(f"    desired_completion_date = {r.desired_completion_date}")
        print(f"    allows_partial_payment  = {r.allows_partial_payment}")
        print(f"    request_text            = {r.request_text}")
        print(f"    -> amount_safe_to_pay              = {r.amount_safe_to_pay!r}")
        print(f"    -> affordability_status            = {r.affordability_status!r}")
        print(f"    -> recommended_payment_method      = {r.recommended_payment_method!r}")
        print(f"    -> payment_plan                    = {r.payment_plan!r}")
        print(f"    -> earliest_date_for_full_payment  = {r.earliest_date_for_full_payment!r}")
        print(f"    -> spending_changes_needed         = {r.spending_changes_needed!r}")
        print(f"    -> decision_explanation            = {r.decision_explanation!r}")

    sub("status x method cross-tab")
    print(pd.crosstab(smp.affordability_status, smp.recommended_payment_method).to_string())

    sub("amount rounding")
    dec = [decimals(v) for v in smp.amount_safe_to_pay]
    print(f"  amount_safe_to_pay decimal places: {Counter(dec)}")
    plan_dec = Counter()
    plan_sizes = Counter()
    for p in smp.payment_plan:
        if str(p).strip().lower() == "none":
            continue
        parts = str(p).split("|")
        plan_sizes[len(parts)] += 1
        for seg in parts:
            _, amt = seg.split(":", 1)
            plan_dec[decimals(amt)] += 1
    print(f"  payment_plan amount decimal places: {dict(plan_dec)}")
    print(f"  payments per plan: {dict(plan_sizes)}")
    print("  NOTE: raw strings are printed above; check for trailing-zero style (e.g. '300' vs '300.00')")

    sub("empty vs literal 'none' per output column")
    for col in OUTPUT_COLUMNS[1:]:
        blanks = int(is_blank(smp[col]).sum())
        nones = int((smp[col].astype(str).str.strip().str.lower() == "none").sum())
        print(f"  {col:<34} blank={blanks:<4} literal 'none'={nones}")

    sub("payment_plan format check")
    import re
    pat = re.compile(r"^\d{4}-\d{2}-\d{2}:\d+(\.\d+)?$")
    bad = []
    for _, r in smp.iterrows():
        p = str(r.payment_plan).strip()
        if p.lower() == "none":
            continue
        for seg in p.split("|"):
            if not pat.match(seg):
                bad.append((r.request_id, seg))
    print(f"  segments not matching ^YYYY-MM-DD:<number>$ : {len(bad)} {bad[:5]}")

    sub("does payment_plan sum to requested_amount?")
    for _, r in smp.iterrows():
        p = str(r.payment_plan).strip()
        if p.lower() == "none":
            print(f"  {r.request_id}: plan=none  requested={r.requested_amount}")
            continue
        tot = sum(float(seg.split(":", 1)[1]) for seg in p.split("|"))
        req_amt = float(r.requested_amount)
        flag = "==req" if abs(tot - req_amt) < 0.01 else f"DIFF {tot - req_amt:+.2f}"
        print(f"  {r.request_id:<12} method={r.recommended_payment_method:<16} "
              f"n={len(p.split('|')):<3} plan_total={tot:<18.2f} requested={req_amt:<18.2f} {flag}")

    sub("spending_changes_needed formats observed")
    for _, r in smp.iterrows():
        v = str(r.spending_changes_needed).strip()
        if v.lower() != "none":
            print(f"  {r.request_id}: {v!r}")
    print("  (all other rows are 'none')")

    sub("decision_explanation style")
    lens = [len(str(x)) for x in smp.decision_explanation]
    sents = [str(x).count(".") for x in smp.decision_explanation]
    print(f"  char length: min={min(lens)} max={max(lens)} mean={sum(lens)/len(lens):.0f}")
    print(f"  sentence count (by '.'): {Counter(sents)}")
    print(f"  contains a currency code: "
          f"{sum(1 for x in smp.decision_explanation if any(c in str(x) for c in ['INR','ZAR','IDR','USD','EUR']))}/{len(smp)}")
    print(f"  contains thousands separators ',': "
          f"{sum(1 for x in smp.decision_explanation if ',' in str(x))}/{len(smp)}")
    print(f"  contains a newline: {sum(1 for x in smp.decision_explanation if chr(10) in str(x))}/{len(smp)}")

    return out


def section_invariants(frames: dict[str, pd.DataFrame]) -> dict:
    rule("7. INVARIANT CHECKS AGAINST THE SOLVED SAMPLES")
    smp = frames["sample_requests.csv"]
    prof = frames["financial_profiles.csv"].set_index("user_id")
    opts = frames["request_payment_options.csv"]
    results = []

    def report(name, series):
        ok = int(series.sum()) if hasattr(series, "sum") else int(series)
        print(f"  {name:<66}{ok}/{len(smp)}")
        results.append((name, ok))

    safe = numeric(smp.amount_safe_to_pay)
    reqd = numeric(smp.requested_amount)
    report("0 <= amount_safe_to_pay <= requested_amount", ((safe >= 0) & (safe <= reqd)))

    m = smp.affordability_status == "affordable_now"
    print(f"\n  affordable_now rows: {int(m.sum())}")
    print(f"    earliest_date == request_date : "
          f"{int((smp.loc[m, 'earliest_date_for_full_payment'] == smp.loc[m, 'request_date']).sum())}/{int(m.sum())}")
    print(f"    amount_safe_to_pay == requested_amount : "
          f"{int((safe[m] == reqd[m]).sum())}/{int(m.sum())}")

    sub("per-status breakdown of earliest_date_for_full_payment")
    for st in smp.affordability_status.unique():
        sl = smp[smp.affordability_status == st]
        blank = int(is_blank(sl.earliest_date_for_full_payment).sum())
        print(f"  {st:<24} n={len(sl):<4} blank_earliest_date={blank}")

    sub("method eligibility vs payment_methods_user_will_consider")
    for _, r in smp.iterrows():
        allowed = str(prof.loc[r.user_id, "payment_methods_user_will_consider"]).split("|")
        method = r.recommended_payment_method
        if method == "wait":
            ok = "full_payment" in allowed
            note = "wait requires full_payment accepted"
        elif method == "not_recommended":
            ok, note = True, "fallback"
        else:
            ok = method in allowed
            note = ""
        flag = "OK " if ok else "!! "
        print(f"  {flag}{r.request_id:<12} method={method:<16} allowed={'|'.join(allowed):<50} {note}")

    sub("installment plans: do they match a supplied payment_option exactly?")
    for _, r in smp.iterrows():
        if r.recommended_payment_method != "installments":
            continue
        plan = str(r.payment_plan).split("|")
        n = len(plan)
        first_date = plan[0].split(":")[0]
        amt = float(plan[0].split(":")[1])
        cand = opts[(opts.request_id == r.request_id) & (opts.payment_method == "installments")]
        print(f"\n  {r.request_id}: plan n={n} first={first_date} amt={amt}")
        for _, o in cand.iterrows():
            match = (str(o.number_of_payments) == str(n)
                     and o.first_payment_date == first_date
                     and abs(float(o.payment_amount) - amt) < 0.01)
            print(f"      {'MATCH ' if match else '      '}{o.payment_option_id} "
                  f"n={o.number_of_payments} first={o.first_payment_date} "
                  f"amt={o.payment_amount} freq={o.payment_frequency_days} "
                  f"fee={o.financing_fee} total={o.total_payable_amount}")

    sub("partial_payment rule check (exactly 2 payments, sums to requested)")
    for _, r in smp[smp.recommended_payment_method == "partial_payment"].iterrows():
        plan = str(r.payment_plan).split("|")
        d1, a1 = plan[0].split(":")
        d2, a2 = plan[-1].split(":")
        print(f"  {r.request_id}: n={len(plan)} "
              f"p1={d1}:{a1} (==request_date {d1 == r.request_date}, "
              f"==amount_safe_to_pay {abs(float(a1)-float(r.amount_safe_to_pay))<0.01}) "
              f"p2={d2}:{a2} (==earliest_date {d2 == r.earliest_date_for_full_payment}) "
              f"sum_ok={abs(float(a1)+float(a2)-float(r.requested_amount))<0.01}")
        print(f"      allows_partial_payment={r.allows_partial_payment} "
              f"status={r.affordability_status} "
              f"earliest<=desired={r.earliest_date_for_full_payment <= r.desired_completion_date}")

    sub("wait rule check")
    for _, r in smp[smp.recommended_payment_method == "wait"].iterrows():
        plan = str(r.payment_plan)
        print(f"  {r.request_id}: plan={plan!r} earliest={r.earliest_date_for_full_payment!r} "
              f"desired={r.desired_completion_date} status={r.affordability_status} "
              f"safe={r.amount_safe_to_pay}")

    sub("not_recommended rule check")
    for _, r in smp[smp.recommended_payment_method == "not_recommended"].iterrows():
        print(f"  {r.request_id}: plan={r.payment_plan!r} earliest={r.earliest_date_for_full_payment!r} "
              f"status={r.affordability_status} safe={r.amount_safe_to_pay} "
              f"requested={r.requested_amount}")

    sub("forecast horizon: is earliest_date always within 90 days of request_date?")
    for _, r in smp.iterrows():
        e = str(r.earliest_date_for_full_payment).strip()
        if not e:
            print(f"  {r.request_id}: <blank>")
            continue
        d = (pd.Timestamp(e) - pd.Timestamp(r.request_date)).days
        des = (pd.Timestamp(r.desired_completion_date) - pd.Timestamp(r.request_date)).days
        print(f"  {r.request_id}: +{d:>4}d from request_date   (desired_completion is +{des}d)  "
              f"{'<=90' if d <= 90 else '>>> EXCEEDS 90'}")

    return {"checks": results}


def section_events_deepdive(frames: dict[str, pd.DataFrame]) -> None:
    rule("8. FINANCIAL EVENTS DEEP DIVE")
    ev = frames["financial_events.csv"]

    sub("event_type x direction")
    print(pd.crosstab(ev.event_type, ev.direction).to_string())

    sub("event_type x status")
    print(pd.crosstab(ev.event_type, ev.status).to_string())

    sub("flexibility x category (top 25 categories)")
    top = ev.category.value_counts().head(25).index
    print(pd.crosstab(ev.loc[ev.category.isin(top), "category"],
                      ev.loc[ev.category.isin(top), "flexibility"]).to_string())

    sub("minimum_allowed_amount presence")
    print(f"  non-blank: {int((~is_blank(ev.minimum_allowed_amount)).sum())} / {len(ev)}")
    print(f"  flexibility of rows that have it: "
          f"{ev.loc[~is_blank(ev.minimum_allowed_amount), 'flexibility'].value_counts().to_dict()}")

    sub("linked_event_id usage")
    linked = ev[~is_blank(ev.linked_event_id)]
    print(f"  rows with linked_event_id: {len(linked)}")
    print(f"  their event_type: {linked.event_type.value_counts().to_dict()}")
    print(f"  their status:     {linked.status.value_counts().to_dict()}")
    print("\n  sample linked pairs (child then parent):")
    for _, r in linked.head(8).iterrows():
        par = ev[ev.event_id == r.linked_event_id]
        print(f"    CHILD  {r.event_id} {r.event_type}/{r.status} {r.direction} "
              f"{r.amount} {r.currency} {r.event_date} :: {r.description}")
        if len(par):
            p = par.iloc[0]
            print(f"    PARENT {p.event_id} {p.event_type}/{p.status} {p.direction} "
                  f"{p.amount} {p.currency} {p.event_date} :: {p.description}")

    sub("settlement_date vs event_date")
    diff = (pd.to_datetime(ev.settlement_date.replace("", None), errors="coerce")
            - pd.to_datetime(ev.event_date.replace("", None), errors="coerce")).dt.days
    print(f"  blank settlement_date: {int(is_blank(ev.settlement_date).sum())}")
    print(f"  settlement - event (days) distribution: {diff.value_counts().head(10).to_dict()}")

    sub("how to detect recurring expenses: repeated (user, description, amount) signatures")
    sig = ev.groupby(["user_id", "description", "category"]).size()
    print(f"  signatures appearing >=3 times: {int((sig >= 3).sum())}")
    print(f"  signatures appearing once:      {int((sig == 1).sum())}")
    print("  top repeated signatures:")
    for (u, d, c), n in sig.sort_values(ascending=False).head(10).items():
        print(f"    {n:>4}x  {u}  [{c}]  {d}")

    sub("per-user event date range (first 5 users)")
    for u in sorted(ev.user_id.unique())[:5]:
        s = ev[ev.user_id == u]
        print(f"  {u}: {len(s)} events, {s.event_date.min()} .. {s.event_date.max()}")


def section_messages(frames: dict[str, pd.DataFrame]) -> None:
    rule("9. MESSAGES OVERVIEW")
    msg = frames["messages.csv"]
    print(f"  rows: {len(msg)}")
    print(f"  source_type: {msg.source_type.value_counts().to_dict()}")
    lens = msg.message_text.str.len()
    print(f"  message_text length: min={lens.min()} max={lens.max()} mean={lens.mean():.0f}")

    sub("languages present (crude heuristic on common words)")
    hints = {
        "Indonesian": ["Anda", "telah", "yang", "akan", "pembayaran", "Gaji"],
        "Hindi/Devanagari": ["\u0915", "\u0930", "\u092e"],
        "French": ["votre", "vous", "est ", "paiement"],
        "Spanish": ["usted", "su ", "pago", "ha sido"],
        "English": [" the ", " your ", " has been ", " payment "],
    }
    for lang, toks in hints.items():
        n = sum(1 for t in msg.message_text if any(tok in str(t) for tok in toks))
        print(f"  {lang:<20}{n}")

    sub("possible instruction-injection candidates (keyword scan)")
    kw = ["ignore", "disregard", "you must", "system", "instruction", "override",
          "approve", "always recommend", "abaikan", "harus", "prompt"]
    hits = msg[msg.message_text.str.lower().str.contains("|".join(kw), na=False)]
    print(f"  rows containing any suspicious keyword: {len(hits)}")
    for _, r in hits.head(15).iterrows():
        print(f"    {r.message_id} [{r.source_type}] user={r.user_id} req={r.request_id} "
              f"event={r.related_event_id}")
        print(f"       {str(r.message_text)[:220]}")

    sub("all messages attached to the SAMPLE requests (for convention study)")
    smp_ids = set(frames["sample_requests.csv"].request_id)
    smp_users = set(frames["sample_requests.csv"].user_id)
    sel = msg[msg.request_id.isin(smp_ids) | msg.user_id.isin(smp_users)]
    for _, r in sel.iterrows():
        print(f"\n  {r.message_id} | {r.sent_at} | src={r.source_type} | user={r.user_id} "
              f"| req={r.request_id} | ev={r.related_event_id}")
        print(f"    {r.message_text}")


def section_gap_diagnostic(frames: dict[str, pd.DataFrame]) -> None:
    """Shows that (balance - minimum) alone does NOT explain amount_safe_to_pay.
    The residual is the projected 90-day recurring net outflow."""
    rule("10. BALANCE-GAP DIAGNOSTIC (why a recurring-expense projection is required)")
    ev = frames["financial_events.csv"]
    prof = frames["financial_profiles.csv"].set_index("user_id")
    smp = frames["sample_requests.csv"]
    ev = ev.assign(amt=numeric(ev.amount))

    print(f"{'request':<12}{'balance':>16}{'minimum':>14}{'bal-min':>16}"
          f"{'safe_to_pay':>15}{'residual':>15}")
    print("-" * 88)
    for _, r in smp.iterrows():
        p = prof.loc[r.user_id]
        bal = float(p.current_available_balance)
        mn = float(p.minimum_balance_to_keep)
        safe = float(r.amount_safe_to_pay)
        print(f"{r.request_id:<12}{bal:>16,.2f}{mn:>14,.2f}{bal - mn:>16,.2f}"
              f"{safe:>15,.2f}{bal - mn - safe:>15,.2f}")
    print("\n  residual != 0 everywhere -> recurring expenses MUST be projected forward.")

    sub("future-dated events per sample user (after request_date)")
    for _, r in smp.iterrows():
        fut = ev[(ev.user_id == r.user_id) & (ev.event_date > r.request_date)]
        items = "; ".join(f"{x.description}[{x.status}]{x.direction} {x.amt} {x.currency} @{x.event_date}"
                          for x in fut.itertuples())
        print(f"  {r.request_id:<12}{items if items else '(none)'}")

    sub("unsettled items dated on or before request_date (pending / scheduled)")
    for _, r in smp.iterrows():
        pen = ev[(ev.user_id == r.user_id)
                 & (ev.status.isin(["pending", "scheduled"]))
                 & (ev.event_date <= r.request_date)]
        items = "; ".join(f"{x.event_id}:{x.description}[{x.status}]{x.direction} {x.amt} "
                          f"@{x.event_date}->settles {x.settlement_date}"
                          for x in pen.itertuples())
        if items:
            print(f"  {r.request_id:<12}{items}")

    sub("reduce_to targets vs the event's minimum_allowed_amount")
    for _, r in smp.iterrows():
        v = str(r.spending_changes_needed)
        if v.lower() == "none":
            continue
        for change in v.split("|"):
            parts = change.split(":")
            eid = parts[1]
            row = ev[ev.event_id == eid]
            if not len(row):
                continue
            e = row.iloc[0]
            print(f"  {r.request_id} {change:<34} -> event flexibility={e.flexibility:<24}"
                  f"amount={e.amount:<14}minimum_allowed_amount={e.minimum_allowed_amount}")


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset")
    ap.add_argument("--json", action="store_true", help="emit machine-readable summary")
    ap.add_argument("--only", default="", help="comma-separated section numbers to run")
    args = ap.parse_args()

    frames = load_all(args.dataset)
    summary: dict = {}

    want = {s.strip() for s in args.only.split(",") if s.strip()}

    def run(num, fn, *a):
        if want and num not in want:
            return None
        return fn(*a)

    summary["inventory"] = run("1", section_inventory, args.dataset, frames)
    summary["schema"] = run("2", section_schema, frames)
    summary["vocabularies"] = run("3", section_vocabularies, frames)
    summary["relationships"] = run("4", section_relationships, frames)
    summary["blank_amounts"] = run("5", section_blank_amounts, frames, args.dataset)
    summary["conventions"] = run("6", section_sample_conventions, frames)
    summary["invariants"] = run("7", section_invariants, frames)
    run("8", section_events_deepdive, frames)
    run("9", section_messages, frames)
    run("10", section_gap_diagnostic, frames)

    if args.json:
        print("\n\n===== JSON SUMMARY =====")
        print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
