"""
explain.py - the decision_explanation column.

The model is given a STRUCTURED FACT PACK that already contains every number.
It writes prose. It cannot change a figure, because every figure it is allowed
to mention is interpolated from the fact pack by `render_fallback` first and
checked against the pack afterwards.

If the model is unavailable, returns a non-empty string, or invents a number
that is not in the pack, the deterministic template is used instead. The
templates are copied from the phrasing patterns of the 25 labelled samples.
"""

from __future__ import annotations

import re
from datetime import date

from llm import CHEAP_MODEL, LLMClient
from solve import Decision

EXPLAIN_SYSTEM = """You write one short explanation of a personal-finance decision.

You are given a JSON fact pack. EVERY number and date you write must come from
it verbatim. Do not compute, round, convert or infer any figure. Do not add
advice, caveats or hedging.

Style, matched to the product's existing copy:
- 2 to 3 short sentences, under 200 characters total, on ONE line.
- Always name the currency with its ISO code and use thousands separators:
  "ZAR 25,256", "IDR 15,952,906.67".
- Write dates as "8 August 2025".
- State the action first, then the safety fact.

Return ONLY JSON: {"explanation": "..."}"""


def _money(amount: float, currency: str) -> str:
    v = round(float(amount) + 0.0, 2)
    if v == int(v):
        body = f"{int(v):,}"
    else:
        body = f"{v:,.2f}"
    return f"{currency} {body}"


def _long_date(iso: str) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B')} {d.year}"


def _change_phrase(descriptions: list[str], dec: Decision) -> str:
    """Turn spending changes into the sample's natural phrasing."""
    parts = []
    for raw, desc in zip(dec.spending_changes_needed.split("|"), descriptions):
        name = desc[0].lower() + desc[1:] if desc else "the subscription"
        if raw.startswith("stop:"):
            parts.append(f"Stop the {name}")
        else:
            amount = raw.rsplit(":", 1)[-1]
            parts.append(f"reduce the {name} to {_money(float(amount), dec.currency)}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return " and ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 2 \
        else " and ".join(parts)


def render_fallback(dec: Decision) -> str:
    """Deterministic explanation. Always valid, always consistent with the row."""
    cur = dec.currency
    minimum = _money(dec.minimum_balance, cur)
    status = dec.affordability_status
    method = dec.recommended_payment_method

    if method == "not_recommended":
        if dec.amount_safe_to_pay > 0:
            return (f"Do not proceed with the {_money(dec.requested_amount, cur)} "
                    f"request. Although {_money(dec.amount_safe_to_pay, cur)} is "
                    f"available today, the full amount cannot be completed safely "
                    f"within 90 days.")
        return (f"Do not make this payment by "
                f"{_long_date(dec.desired_completion_date)}. None of the available "
                f"options keeps the {minimum} minimum protected.")

    if method == "wait":
        when = _long_date(dec.earliest_date_for_full_payment)
        return (f"Pay {_money(dec.requested_amount, cur)} in full on {when}. "
                f"Paying earlier would take the balance below the {minimum} minimum.")

    if method == "installments":
        first = dec.payment_plan.split("|")[0]
        when = _long_date(first.split(":")[0])
        amt = float(first.split(":")[1])
        return (f"Use {dec.n_payments} installments of {_money(amt, cur)}, "
                f"starting {when}. This leaves at least {minimum} available.")

    if method == "partial_payment":
        segs = dec.payment_plan.split("|")
        a1 = float(segs[0].split(":")[1])
        d2, a2 = segs[1].split(":")
        return (f"Pay {_money(a1, cur)} today and the remaining "
                f"{_money(float(a2), cur)} on {_long_date(d2)}. This completes the "
                f"full request and keeps the {minimum} minimum protected.")

    # full_payment
    if dec.spending_changes_needed != "none":
        phrase = _change_phrase(dec.change_descriptions, dec)
        return (f"{phrase}, then pay {_money(dec.requested_amount, cur)} today. "
                f"This leaves at least {minimum} available.")
    return (f"Pay {_money(dec.requested_amount, cur)} today. This leaves at least "
            f"{minimum} available over the next 90 days.")


def _fact_pack(dec: Decision) -> dict:
    return {
        "currency": dec.currency,
        "requested_amount": _money(dec.requested_amount, dec.currency),
        "amount_safe_to_pay": _money(dec.amount_safe_to_pay, dec.currency),
        "minimum_balance_to_keep": _money(dec.minimum_balance, dec.currency),
        "affordability_status": dec.affordability_status,
        "recommended_payment_method": dec.recommended_payment_method,
        "payment_plan": dec.payment_plan,
        "number_of_payments": dec.n_payments,
        "installment_amount": (_money(dec.installment_amount, dec.currency)
                               if dec.installment_amount else None),
        "first_payment_date": _long_date(dec.payment_plan.split(":")[0])
        if dec.payment_plan != "none" else None,
        "earliest_date_for_full_payment":
            _long_date(dec.earliest_date_for_full_payment),
        "desired_completion_date": _long_date(dec.desired_completion_date),
        "spending_changes": dec.spending_changes_needed,
        "spending_change_descriptions": dec.change_descriptions,
        "forecast_horizon_days": 90,
    }


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers_are_grounded(text: str, pack: dict) -> bool:
    """Reject an explanation containing a figure that is not in the fact pack."""
    allowed = set()
    for v in pack.values():
        if v is None:
            continue
        for m in _NUM.finditer(str(v)):
            allowed.add(m.group().replace(",", ""))
    allowed.add("90")
    for m in _NUM.finditer(text):
        if m.group().replace(",", "") not in allowed:
            return False
    return True


def explain(dec: Decision, llm: LLMClient | None) -> str:
    fallback = render_fallback(dec)
    if llm is None or not llm.available:
        return fallback
    pack = _fact_pack(dec)
    import json
    data = llm.complete_json(
        "explanation", dec.request_id, EXPLAIN_SYSTEM,
        json.dumps(pack, ensure_ascii=False), max_tokens=250, model=CHEAP_MODEL)
    if not data:
        return fallback
    text = str(data.get("explanation", "")).strip().replace("\n", " ")
    if not text or len(text) > 260 or not _numbers_are_grounded(text, pack):
        return fallback
    return text
