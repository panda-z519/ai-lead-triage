# AI Lead Triage

Every inbound message (web form, inbox, chat widget) is classified and turned into a clean
row: category, urgency, company, need, budget and a one-line summary. Urgent sales leads
trigger a Slack alert; spam never reaches your sheet.

```
Web form / email ──► n8n webhook ──► this API (AI extraction) ──► Google Sheet
                                                         └──► Slack alert if urgent
```

## Why it is built this way

- **Never loses a lead.** If the AI call times out or returns bad JSON, the service retries
  rate limits and server errors a bounded number of times, then falls back to a
  rule-based extractor instead of failing.
- **Safe to retry.** Each lead gets a stable id, so a re-delivered webhook does not create a
  duplicate row.
- **Works without an AI key.** With no key configured, the rule-based extractor runs for
  free, which is handy for demos and tests.
- **Provider-agnostic.** Any OpenAI-compatible chat completions endpoint can be plugged in
  through environment variables.

## Example (response trimmed)

```bash
curl -s localhost:8000/leads -H 'content-type: application/json' -d '{
  "name": "Dana", "email": "dana@example.com",
  "message": "I am at Acme Labs and want a quote to automate invoicing. Budget about $3k. ASAP."
}'
```

```json
{
  "duplicate": false,
  "lead": {
    "category": "sales_inquiry", "urgency": "high", "company": "Acme Labs",
    "budget": "$3,000", "summary": "I am at Acme Labs and want a quote to automate invoicing.",
    "extractor": "rules"
  }
}
```

## Run it

```bash
uv sync
uv run uvicorn lead_triage.app:app --reload     # API on http://localhost:8000
uv run pytest                                   # tests
```

Or with Docker:

```bash
docker build -t lead-triage .
docker run -p 8000:8000 --env-file .env lead-triage
```

Configuration lives in `.env` (see `.env.example`). Import `n8n/lead-triage-workflow.json`
into n8n, then set your Google Sheet URL and Slack channel on the last two nodes.
