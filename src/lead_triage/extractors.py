"""Turn a raw message into a TriagedLead.

Two implementations share one interface:
- RuleBasedExtractor: free, deterministic, works offline. Used for demos and tests.
- LLMExtractor: any OpenAI-compatible chat completions API, with a timeout,
  bounded retries and a fallback to the rule-based extractor.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Protocol

import httpx

from lead_triage.models import Category, InboundLead, TriagedLead, Urgency

logger = logging.getLogger(__name__)

_SPAM_HINTS = ("seo services", "guest post", "crypto", "backlinks", "casino", "loan offer")
_SUPPORT_HINTS = ("not working", "error", "bug", "refund", "can't log in", "cannot log in", "broken")
_SALES_HINTS = ("quote", "pricing", "price", "proposal", "hire", "interested in", "budget", "project")
_PARTNER_HINTS = ("partnership", "partner with", "collaborate", "affiliate", "reseller")
_URGENT_HINTS = ("urgent", "asap", "today", "immediately", "down", "outage")
_BUDGET_RE = re.compile(r"(?:\$|usd\s?)\s?(\d[\d,]*(?:\.\d+)?)\s?(k)?", re.IGNORECASE)
_COMPANY_RE = re.compile(
    r"\b(?:at|from|with)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})", re.MULTILINE
)


class Extractor(Protocol):
    name: str

    def extract(self, lead: InboundLead) -> TriagedLead: ...


class RuleBasedExtractor:
    name = "rules"

    def extract(self, lead: InboundLead) -> TriagedLead:
        text = f"{lead.subject or ''}\n{lead.message}"
        lowered = text.lower()
        category = _classify(lowered)
        urgency = Urgency.HIGH if any(h in lowered for h in _URGENT_HINTS) else Urgency.NORMAL
        if category is Category.SPAM:
            urgency = Urgency.LOW
        return TriagedLead(
            lead_id=lead.lead_id(),
            source=lead.source,
            category=category,
            urgency=urgency,
            name=lead.name,
            email=lead.email,
            company=_find_company(text),
            need=_first_sentence(lead.message),
            budget=_find_budget(text),
            summary=_first_sentence(lead.message, limit=160),
            extractor=self.name,
        )


def _classify(lowered: str) -> Category:
    # Order matters: spam first, then the most specific intents.
    for hints, category in (
        (_SPAM_HINTS, Category.SPAM),
        (_SUPPORT_HINTS, Category.SUPPORT),
        (_PARTNER_HINTS, Category.PARTNERSHIP),
        (_SALES_HINTS, Category.SALES_INQUIRY),
    ):
        if any(h in lowered for h in hints):
            return category
    return Category.OTHER


def _find_budget(text: str) -> str | None:
    match = _BUDGET_RE.search(text)
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    if match.group(2):
        amount *= 1000
    return f"${amount:,.0f}"


def _find_company(text: str) -> str | None:
    match = _COMPANY_RE.search(text)
    return match.group(1).strip(" .") if match else None


def _first_sentence(text: str, limit: int = 240) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


_SYSTEM_PROMPT = """You triage inbound business leads. Reply with JSON only, using keys:
category (one of: sales_inquiry, support, partnership, spam, other),
urgency (one of: low, normal, high),
company (string or null), need (one sentence or null),
budget (string like "$5,000" or null), summary (max 160 characters).
Only use facts present in the message. Never invent a budget or company."""


class LLMExtractor:
    name = "llm"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        fallback: Extractor | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._max_attempts = max_attempts
        self._fallback = fallback or RuleBasedExtractor()
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def extract(self, lead: InboundLead) -> TriagedLead:
        try:
            fields = self._call(lead)
            return TriagedLead(
                lead_id=lead.lead_id(),
                source=lead.source,
                category=Category(fields["category"]),
                urgency=Urgency(fields["urgency"]),
                name=lead.name,
                email=lead.email,
                company=fields.get("company"),
                need=fields.get("need"),
                budget=fields.get("budget"),
                summary=str(fields.get("summary") or "")[:160] or _first_sentence(lead.message),
                extractor=self.name,
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # A failed AI call must never lose the lead: fall back and say so in the logs.
            logger.warning(
                "llm_extract_failed", extra={"lead_id": lead.lead_id(), "error": type(exc).__name__}
            )
            return self._fallback.extract(lead)

    def _call(self, lead: InboundLead) -> dict:
        payload = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Subject: {lead.subject or ''}\n\n{lead.message}",
                },
            ],
        }
        for attempt in range(1, self._max_attempts + 1):
            response = self._client.post(self._url, json=payload, headers=self._headers)
            # Retry only what can recover: rate limits and server errors.
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self._max_attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        raise httpx.HTTPError("unreachable")
