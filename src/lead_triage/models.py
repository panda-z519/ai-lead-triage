"""Request and response models."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, EmailStr, Field


class Category(StrEnum):
    SALES_INQUIRY = "sales_inquiry"
    SUPPORT = "support"
    PARTNERSHIP = "partnership"
    SPAM = "spam"
    OTHER = "other"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class InboundLead(BaseModel):
    """A raw message from a web form, an inbox or a chat widget."""

    source: str = Field(default="web_form", max_length=50)
    name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    subject: str | None = Field(default=None, max_length=300)
    message: str = Field(min_length=1, max_length=10_000)

    def lead_id(self) -> str:
        """Stable id so retries from n8n/Zapier never create duplicate rows."""
        raw = "|".join([self.source, (self.email or "").lower(), self.message.strip()])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class TriagedLead(BaseModel):
    """Structured result that downstream tools (Sheets, CRM, Slack) consume."""

    lead_id: str
    source: str
    category: Category
    urgency: Urgency
    name: str | None = None
    email: str | None = None
    company: str | None = None
    need: str | None = None
    budget: str | None = None
    summary: str
    extractor: str
