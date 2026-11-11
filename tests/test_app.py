from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from lead_triage.app import create_app
from lead_triage.extractors import LLMExtractor, RuleBasedExtractor
from lead_triage.models import InboundLead
from lead_triage.storage import CsvLeadStore


def _client(tmp_path: Path) -> TestClient:
    store = CsvLeadStore(tmp_path / "leads.csv")
    return TestClient(create_app(RuleBasedExtractor(), store))


def test_sales_lead_extracts_budget_and_company(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "name": "Dana",
        "email": "dana@example.com",
        "message": "Hi, I'm at Acme Labs and want a quote to automate invoicing. "
        "Budget is about $3k. We need it ASAP.",
    }
    data = client.post("/leads", json=body).json()
    lead = data["lead"]
    assert data["duplicate"] is False
    assert lead["category"] == "sales_inquiry"
    assert lead["urgency"] == "high"
    assert lead["budget"] == "$3,000"
    assert lead["company"] == "Acme Labs"


def test_spam_is_low_urgency(tmp_path: Path) -> None:
    client = _client(tmp_path)
    lead = client.post(
        "/leads", json={"message": "Urgent! Cheap SEO services and backlinks for you."}
    ).json()["lead"]
    assert lead["category"] == "spam"
    assert lead["urgency"] == "low"


def test_retry_from_n8n_is_deduplicated(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {"email": "a@example.com", "message": "The export button is not working."}
    first = client.post("/leads", json=body).json()
    second = client.post("/leads", json=body).json()
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    rows = (tmp_path / "leads.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 2  # header + one lead


def test_invalid_payload_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/leads", json={"message": ""}).status_code == 422
    assert client.post("/leads", json={"message": "hi", "email": "nope"}).status_code == 422


def _llm(handler) -> LLMExtractor:
    transport = httpx.MockTransport(handler)
    return LLMExtractor(
        "https://llm.test/v1",
        "key",
        "model",
        client=httpx.Client(transport=transport),
        max_attempts=2,
    )


def test_llm_extractor_parses_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = (
            '{"category":"partnership","urgency":"normal","company":"Blue Co",'
            '"need":"Co-marketing","budget":null,"summary":"Wants to partner."}'
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    lead = _llm(handler).extract(InboundLead(message="Let's partner up."))
    assert lead.category == "partnership"
    assert lead.company == "Blue Co"
    assert lead.extractor == "llm"


def test_llm_failure_falls_back_to_rules_without_losing_lead(monkeypatch) -> None:
    monkeypatch.setattr("lead_triage.extractors.time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    lead = _llm(handler).extract(InboundLead(message="I need a price quote for a project."))
    assert calls["n"] == 2  # bounded retries
    assert lead.extractor == "rules"
    assert lead.category == "sales_inquiry"


def test_llm_bad_category_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = '{"category":"weird","urgency":"normal","summary":"x"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    lead = _llm(handler).extract(InboundLead(message="Hello there."))
    assert lead.extractor == "rules"
