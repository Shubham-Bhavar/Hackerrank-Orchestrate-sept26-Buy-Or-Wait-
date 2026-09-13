"""
llm.py - a thin, auditable wrapper around the Anthropic Messages API.

Design goals, in order:
  1. Every call is logged with provider, model, purpose, tokens and cost.
  2. Every call is cached on disk by a hash of its exact input, so a re-run
     costs nothing and produces byte-identical evidence.
  3. The absence of an API key degrades to a deterministic fallback rather
     than crashing the pipeline.

There is deliberately no framework here. The whole client is ~150 lines so it
can be read and defended line by line.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# USD per million tokens. Update if pricing changes.
PRICING = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
}
DEFAULT_MODEL = "claude-sonnet-4-6"
CHEAP_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class CallRecord:
    provider: str
    model: str
    purpose: str
    subject_id: str
    input_tokens: int
    output_tokens: int
    cached: bool
    latency_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        p = PRICING.get(self.model)
        if not p:
            return 0.0
        return (self.input_tokens * p["input"] + self.output_tokens * p["output"]) / 1e6


@dataclass
class UsageLedger:
    records: list[CallRecord] = field(default_factory=list)

    def add(self, rec: CallRecord) -> None:
        self.records.append(rec)

    @property
    def live(self) -> list[CallRecord]:
        return [r for r in self.records if not r.cached]

    def totals(self) -> dict:
        live = self.live
        by_model: dict[str, dict] = {}
        for r in live:
            m = by_model.setdefault(r.model, {
                "provider": r.provider, "calls": 0, "input_tokens": 0,
                "output_tokens": 0, "cost_usd": 0.0})
            m["calls"] += 1
            m["input_tokens"] += r.input_tokens
            m["output_tokens"] += r.output_tokens
            m["cost_usd"] += r.cost_usd
        return {
            "live_calls": len(live),
            "cache_hits": len(self.records) - len(live),
            "input_tokens": sum(r.input_tokens for r in live),
            "output_tokens": sum(r.output_tokens for r in live),
            "total_tokens": sum(r.total_tokens for r in live),
            "cost_usd": sum(r.cost_usd for r in live),
            "by_model": by_model,
            "by_purpose": {
                p: sum(1 for r in live if r.purpose == p)
                for p in sorted({r.purpose for r in live})
            },
        }

    def write_report(self, path: str, n_requests: int, mode: str,
                     notes: str = "") -> None:
        """Write evaluation/usage_report.md for the run that produced output.csv."""
        t = self.totals()
        avg = t["total_tokens"] / n_requests if n_requests else 0.0
        per = t["cost_usd"] / n_requests if n_requests else 0.0
        L = ["# Token Usage and Cost Report", ""]
        L.append("Covers the final full-dataset run that produced `output.csv`.")
        L.append("")
        L.append(f"- Provider: **Anthropic**")
        L.append(f"- Models configured: **{DEFAULT_MODEL}** (text + vision), "
                 f"**{CHEAP_MODEL}** (message classification)")
        L.append(f"- Temperature: **0** on every call")
        L.append(f"- Evidence mode for this run: **{mode}**")
        L.append(f"- Requests scored: **{n_requests}**")
        L.append(f"- Live model calls: **{t['live_calls']}**")
        L.append(f"- Cache hits (calls avoided): **{t['cache_hits']}**")
        L.append(f"- Input tokens: **{t['input_tokens']:,}**")
        L.append(f"- Output tokens: **{t['output_tokens']:,}**")
        L.append(f"- Total tokens: **{t['total_tokens']:,}**")
        L.append(f"- Average tokens per request: **{avg:,.1f}**")
        L.append(f"- Estimated total cost: **${t['cost_usd']:,.4f}**")
        L.append(f"- Estimated cost per request: **${per:,.6f}**")
        L.append("")
        L.append("## Per-model totals")
        L.append("")
        L.append("| provider | model | calls | input | output | total | cost (USD) |")
        L.append("|---|---|---:|---:|---:|---:|---:|")
        if t["by_model"]:
            for m, d in sorted(t["by_model"].items()):
                L.append(f"| {d['provider']} | {m} | {d['calls']} | "
                         f"{d['input_tokens']:,} | {d['output_tokens']:,} | "
                         f"{d['input_tokens'] + d['output_tokens']:,} | "
                         f"${d['cost_usd']:.4f} |")
        else:
            for m in (DEFAULT_MODEL, CHEAP_MODEL):
                L.append(f"| Anthropic | {m} | 0 | 0 | 0 | 0 | $0.0000 |")
            L.append("")
            L.append("No live calls were made in this run; the models above are the "
                     "ones the code is configured to use. Figures are zero because "
                     "they were measured, not because they were estimated.")
        L.append("")
        L.append("## Calls by purpose")
        L.append("")
        L.append("| purpose | live calls | model | expected volume with a key |")
        L.append("|---|---:|---|---|")
        if t["by_purpose"]:
            for p, n in t["by_purpose"].items():
                L.append(f"| {p} | {n} | - | - |")
        else:
            L.append(f"| image_amount_extraction | 0 | {DEFAULT_MODEL} | "
                     "16 (one per blank-amount event in the whole dataset) |")
            L.append(f"| message_classification | 0 | {CHEAP_MODEL} | "
                     "<= 215 (one per message, cached by content hash) |")
            L.append(f"| explanation | 0 | {CHEAP_MODEL} | "
                     "<= 250 (one per request, deterministic fallback on failure) |")
        L.append("")
        L.append("## Overall totals")
        L.append("")
        L.append("| metric | value |")
        L.append("|---|---:|")
        L.append(f"| live calls | {t['live_calls']} |")
        L.append(f"| cache hits | {t['cache_hits']} |")
        L.append(f"| input tokens | {t['input_tokens']:,} |")
        L.append(f"| output tokens | {t['output_tokens']:,} |")
        L.append(f"| total tokens | {t['total_tokens']:,} |")
        L.append(f"| estimated cost | ${t['cost_usd']:,.4f} |")
        L.append("")
        L.append("## Pricing basis")
        L.append("")
        L.append("| model | input $/Mtok | output $/Mtok |")
        L.append("|---|---:|---:|")
        for m, p in sorted(PRICING.items()):
            L.append(f"| {m} | {p['input']:.2f} | {p['output']:.2f} |")
        if notes:
            L.append("")
            L.append("## Notes")
            L.append("")
            L.append(notes)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(L) + "\n")

    def dump(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"totals": self.totals(),
                       "records": [asdict(r) for r in self.records]}, fh, indent=2)


def estimate_tokens(text: str) -> int:
    """Rough token count used only for cold-run projections, never for billing."""
    return max(1, len(text) // 4)


class LLMClient:
    """Anthropic Messages client with an on-disk response cache.

    `available` is False when no API key is present. Callers must check it and
    fall back to a deterministic path; the client never invents a response.
    """

    def __init__(self, cache_path: str, ledger: UsageLedger,
                 model: str = DEFAULT_MODEL, offline: bool = False):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self.ledger = ledger
        self.cache_path = cache_path
        self.offline = offline
        self.cache: dict = {}
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as fh:
                self.cache = json.load(fh)

    @property
    def available(self) -> bool:
        return bool(self.api_key) and not self.offline

    # ------------------------------------------------------------------
    @staticmethod
    def _key(model: str, purpose: str, payload: str) -> str:
        h = hashlib.sha256(f"{model}\0{purpose}\0{payload}".encode()).hexdigest()
        return f"{purpose}:{h[:32]}"

    def save_cache(self) -> None:
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.cache, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.cache_path)

    # ------------------------------------------------------------------
    def complete_json(self, purpose: str, subject_id: str, system: str,
                      content: list | str, max_tokens: int = 1200,
                      model: str | None = None, retries: int = 1) -> dict | None:
        """Ask for a JSON object. Returns the parsed dict, or None on failure.

        The cache key is the full (model, purpose, system+content) payload, so
        an identical request never bills twice.
        """
        model = model or self.model
        payload = json.dumps({"system": system, "content": content},
                             sort_keys=True, ensure_ascii=False)
        key = self._key(model, purpose, payload)

        if key in self.cache:
            entry = self.cache[key]
            self.ledger.add(CallRecord(
                "anthropic", model, purpose, subject_id,
                entry.get("input_tokens", 0), entry.get("output_tokens", 0),
                cached=True))
            return entry["result"]

        if not self.available:
            return None

        body = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user",
                          "content": content if isinstance(content, list)
                          else [{"type": "text", "text": content}]}],
        }).encode()

        for attempt in range(retries + 1):
            started = time.time()
            try:
                req = urllib.request.Request(API_URL, data=body, headers={
                    "content-type": "application/json",
                    "anthropic-version": API_VERSION,
                    "x-api-key": self.api_key,
                })
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read())
                text = "".join(b.get("text", "") for b in data.get("content", [])
                               if b.get("type") == "text")
                usage = data.get("usage", {})
                parsed = _parse_json(text)
                if parsed is None:
                    raise ValueError("model did not return parseable JSON")
                self.cache[key] = {
                    "result": parsed,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                }
                self.ledger.add(CallRecord(
                    "anthropic", model, purpose, subject_id,
                    usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                    cached=False, latency_ms=int((time.time() - started) * 1000)))
                return parsed
            except (urllib.error.URLError, ValueError, KeyError, TimeoutError):
                if attempt >= retries:
                    return None
                time.sleep(1.5 * (attempt + 1))
        return None


def _parse_json(text: str) -> dict | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return None
