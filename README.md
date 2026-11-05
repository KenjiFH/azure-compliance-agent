# Compliance Agent PoC

Slack-triggered vendor contract compliance agent. Uploading a PDF contract to Slack runs OCR, RAG policy checks, and liability math; violations escalate to Slack, Trello, and Jira. Each violation is traced back to the exact quote, page and bounding box in the PDF, and a signed review link opens a viewer where a human can confirm or dismiss it.

## Architecture

![Azure architecture](architecture/azure-architecture.png)


Interactive version: [open the diagram](https://kenjifh.github.io/azure-compliance-agent/) (served by GitHub Pages from `docs/architecture.html`). Earlier diagrams are kept in `architecture/v1.png` (original target design) and `architecture/v2.png` (local PoC).


1. Slack posts a file event to FastAPI (`POST /slack/events`).
2. The API returns `200` immediately and processes in a background task.
3. Azure Document Intelligence (`prebuilt-read`) extracts the text plus per-line page numbers, character offsets and bounding polygons. Only the text goes to the LLM; the geometry stays server-side.
4. A LangGraph ReAct agent (Azure OpenAI) reviews the text against company policies retrieved from Azure AI Search and a liability calculator.
5. A second structured LLM pass (`extract_findings`) turns the report into discrete findings, each with a **verbatim quote** from the contract (never coordinates).
6. `locator.py` matches each quote to the OCR lines (exact match after whitespace/case normalisation, then fuzzy fallback) to get page, character range and boxes. Unmatched quotes are flagged `located=False`.
7. The PDF and `review.json` are saved to Azure Blob Storage.
8. The scorecard is written back to the Slack thread. Violations also create Slack, Trello, and Jira tickets that include each finding's page and quote and a signed link to the review page.
9. The reviewer opens `/reviews/{id}`, sees the PDF with the violation highlighted, and confirms or dismisses each finding; decisions are saved back to Blob.

Policy documents in `data/` are indexed into Azure AI Search by `ingest_policies.py`; the app only queries the index at runtime, so it starts fast and needs no embedding calls at boot.

## Pipeline

```
Slack PDF upload
  → FastAPI webhook (idempotent, <3s ACK)
  → Background task: download PDF
  → Azure Document Intelligence (prebuilt-read: text + line polygons)
  → LangGraph agent (Azure OpenAI)
       ├─ company_policy_retriever (Azure AI Search)
       └─ calculate_liability_difference
  → extract_findings (structured output, verbatim quotes)
  → locator (quote → page + bounding boxes)
  → Blob Storage (PDF + review.json)
  → Slack thread scorecard
  → On violation: Slack webhook + Trello card + Jira task (with quotes + review link)
  → Human review page (/reviews/{id}): highlighted PDF, confirm / dismiss
```

Escalation tools are invoked deterministically in Python when the scorecard contains `action required` or `🚨`, not by the LLM. The agent only uses the policy retriever and liability calculator.

## Project layout

| Path | Role |
|------|------|
| `main.py` | FastAPI app, Slack webhook, background processing, routing |
| `agent.py` | LangGraph ReAct agent + system prompt; `extract_findings` structured pass |
| `models.py` | Pydantic models: parsed document, findings, review |
| `locator.py` | Resolves a verbatim quote to page, char range and bounding boxes |
| `storage.py` | Azure Blob persistence for the PDF and `review.json` |
| `review.py` | Review endpoints, HMAC-signed links, approve/dismiss |
| `static/review.html` | Review viewer (PDF.js + highlight overlay) |
| `tests/` | pytest tests for the locator |
| `tools.py` | Liability calc, Slack/Trello/Jira escalation helpers |
| `ingestion.py` | Azure AI Search vector store + document loading/chunking helpers |
| `ingest_policies.py` | Loads `data/*.txt` into the Azure AI Search index (re-run when policies change) |
| `document_parser.py` | Azure Document Intelligence extraction (text + line geometry) |
| `test_connection.py` | Azure OpenAI connectivity check |
| `Dockerfile`, `.dockerignore` | Container image for Azure Container Apps |
| `data/` | Company policy source files |
| `vendor_example/` | Sample MSA PDF for local parser tests |
| `architecture/` | Static diagram images |
| `docs/` | Interactive architecture diagram published via GitHub Pages (generated with the Archify skill) |

## Prerequisites

- Python 3.11+
- Azure OpenAI (chat + embeddings deployments)
- Azure AI Document Intelligence
- Azure AI Search
- Azure Storage account (Blob) for PDFs and review data
- Docker and Azure CLI (for deployment)
- Slack app (bot token, Events API pointing at `/slack/events`, optional incoming webhook)
- Optional: Trello API credentials, Jira Cloud API token

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` in the project root with the variables below.

### Environment variables

Create a `.env` in the project root (never commit secrets):

```bash
# Azure OpenAI
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT_NAME=
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME=
AZURE_OPENAI_API_VERSION=

# Azure Document Intelligence
DOC_INTEL_ENDPOINT=
DOC_INTEL_KEY=

# Azure AI Search
AZURE_SEARCH_ENDPOINT=      # https://<name>.search.windows.net
AZURE_SEARCH_KEY=
AZURE_SEARCH_INDEX_NAME=    # e.g. compliance-policies

# Review viewer
REVIEW_LINK_SECRET=         # random string used to sign review links
PUBLIC_BASE_URL=            # public URL of this service, e.g. https://<app>.azurecontainerapps.io
AZURE_STORAGE_CONNECTION_STRING=   # local/Azurite; or use the account URL below with managed identity
AZURE_STORAGE_ACCOUNT_URL=         # https://<account>.blob.core.windows.net
REVIEW_CONTAINER=reviews           # optional, blob container name

# Slack
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
SLACK_WEBHOOK_URL=          # #legal-escalations (or equivalent)

# Trello (optional escalations)
TRELLO_API_KEY=
TRELLO_TOKEN=
TRELLO_LIST_ID=

# Jira (optional escalations)
JIRA_DOMAIN=                # https://your-org.atlassian.net
JIRA_EMAIL=
JIRA_API_TOKEN=
JIRA_PROJECT_KEY=

# LangSmith (optional tracing)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=
```

## Run

Populate the policy index once (and again whenever `data/` changes):

```bash
python ingest_policies.py
```

Then start the server:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or with Docker:

```bash
docker build -t compliance-agent .
docker run --env-file .env -p 8000:8000 compliance-agent
```

Expose the service (ngrok, or the Container Apps URL below) and set the Slack Events Request URL to:

```
https://<host>/slack/events
```

Subscribe to `file_shared`, `message.channels` / `app_mention` as needed. Upload a vendor PDF in a channel the bot can access.

### Local smoke tests

```bash
python test_connection.py   # Azure OpenAI handshake
python ingestion.py         # Query the AI Search index with a sample policy question
python document_parser.py   # OCR sample PDF under vendor_example/ (prints page/line counts)
pytest tests                # locator unit tests (no Azure needed)
python tools.py             # Fire Slack/Trello test payloads
```

## Deploy to Azure Container Apps

The app runs as a single-replica Azure Container App in resource group `rg-west-deployments-kenji` (westus). The image lives in Azure Container Registry `myacrreg` and is pulled with a user-assigned managed identity that has `AcrPull`.

```bash
# Build the image in ACR
az acr build -r myacrreg -g rg-west-deployments-kenji -t compliance-agent:v2 .

# Roll out a new version
az containerapp update -g rg-west-deployments-kenji -n compliance-agent \
  --image myacrreg.azurecr.io/compliance-agent:v2
```

- API keys, tokens and webhook URLs are stored as Container Apps secrets and exposed to the app as env vars (`secretref:`); endpoints and deployment names are plain env vars.
- Grant the app's managed identity **Storage Blob Data Contributor** on the storage account and set `AZURE_STORAGE_ACCOUNT_URL`, `REVIEW_LINK_SECRET` and `PUBLIC_BASE_URL`.
- Keep `--max-replicas 1`: file-ID dedup is in-memory, so more replicas would process Slack retries twice.
- The container has no `.env` file; `load_dotenv()` only warns and configuration comes from the environment.
- The `gpt-4o-deploy` deployment name in Azure OpenAI currently points at a **gpt-5.1** model (gpt-4o had no quota); the name is kept so no app config changes.
- Document Intelligence is on the free F0 tier, which analyses only the first 2 pages of each PDF. Use S0 for full contracts.

## Agent behavior

- Temperature `0.0` for deterministic reviews.
- Retrieves top-3 policy chunks before deciding.
- Emits a fixed Slack markdown **Compliance Scorecard** (vendor name, overall status, financial review, security/data residency).
- Sample policy rules live in `data/security_policy.txt` (SOC 2 / GDPR, $5M liability floor, US-East-2 / US-West-2 hosting).

## Violation review

- Findings are located by quote, not by LLM-supplied coordinates, so highlights are deterministic. If a quote can't be found, the card shows "Could not locate in document" instead of guessing.
- Review links are HMAC-signed and expire after 7 days (`/reviews/{id}?exp=...&sig=...`). Anyone with the link can view and decide, so treat it like a secret.
- If building the review page fails, escalation to Slack/Trello/Jira still goes out (without the link).

## Operational notes

- The `/slack/events` endpoint does not yet verify Slack request signatures (`SLACK_SIGNING_SECRET` is configured but unused); add this before relying on it publicly.
- Bot messages are ignored to avoid reply loops.
- File IDs are cached ~5 minutes to suppress Slack retry duplicates.
- Background failures update the Slack “thinking” message with an error string.
- LangSmith run metadata tags Slack channel / thread when tracing is enabled.
