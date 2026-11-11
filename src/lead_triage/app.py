"""HTTP API. n8n or Zapier posts each new lead here and routes on the response."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI

from lead_triage.extractors import Extractor, LLMExtractor, RuleBasedExtractor
from lead_triage.models import InboundLead, TriagedLead
from lead_triage.storage import CsvLeadStore

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='{"time":"%(asctime)s","level":"%(levelname)s","event":"%(message)s"}',
)
logger = logging.getLogger("lead_triage")


def build_extractor() -> Extractor:
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "")
    model = os.getenv("LLM_MODEL", "")
    if api_key and base_url and model:
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
        return LLMExtractor(base_url, api_key, model, timeout_seconds=timeout)
    return RuleBasedExtractor()


def create_app(extractor: Extractor | None = None, store: CsvLeadStore | None = None) -> FastAPI:
    app = FastAPI(title="AI Lead Triage", version="0.1.0")
    app.state.extractor = extractor or build_extractor()
    app.state.store = store or CsvLeadStore(Path(os.getenv("LEADS_CSV_PATH", "data/leads.csv")))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "extractor": app.state.extractor.name}

    @app.post("/leads", response_model=dict)
    def triage(lead: InboundLead) -> dict:
        result: TriagedLead = app.state.extractor.extract(lead)
        is_new = app.state.store.save(result)
        logger.info(
            "lead_triaged lead_id=%s category=%s urgency=%s new=%s",
            result.lead_id,
            result.category,
            result.urgency,
            is_new,
        )
        return {"duplicate": not is_new, "lead": result.model_dump(mode="json")}

    return app


app = create_app()
