import os
import requests
import tempfile
from fastapi import FastAPI, BackgroundTasks, Request
from dotenv import load_dotenv
from agent import app_agent
from document_parser import parse_document, ExtractionError
from agent import extract_findings
from locator import locate
from models import Review
from review import router as review_router, make_review_url
import storage
import uuid
import dedup  # Redis-backed idempotency (SET NX EX), shared across replicas
from starlette.concurrency import run_in_threadpool

from tools import escalate_to_slack, create_trello_card, create_jira_ticket


from langchain_core.runnables.config import RunnableConfig
from langsmith import Client

# Initialize the LangSmith client (useful if we want to programmatically push feedback later)
ls_client = Client()

load_dotenv()

app = FastAPI(title="Logic20/20 Compliance Agent")
app.include_router(review_router)
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

def build_review(parsed, pdf_bytes: bytes, vendor: str, contract_text: str, report: str):
    """Extract findings, locate them in the PDF, persist PDF+review. Returns (url, findings)."""
    drafts = extract_findings(contract_text, report)
    findings = [locate(d, parsed, uuid.uuid4().hex[:8]) for d in drafts]
    review = Review(id=uuid.uuid4().hex, vendor=vendor, pages=parsed.pages, findings=findings)
    storage.save_pdf(review.id, pdf_bytes)
    storage.save_review(review)
    return make_review_url(review.id), findings


def review_url_footer(url: str, findings) -> str:
    lines = ["\n\n*📍 Violation locations (click to review):*"]
    for f in findings:
        where = f"p.{f.page}" if f.located else "location not found"
        lines.append(f"• {f.policy_rule} ({where}): “{f.quote[:140]}”")
    lines.append(f"<{url}|Open review page>")
    return "\n".join(lines)


def process_slack_file_background(file_url: str, channel_id: str, thread_ts: str, file_id: str):
    """
    Background worker with Enterprise UX State Management (Thinking -> Update).
    """
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    thinking_ts = None

    try:
        # --- UX STATE 1: THE "THINKING" MESSAGE ---
        print(f"\n[1/5] Posting 'Thinking' status to Slack...")
        thinking_payload = {
            "channel": channel_id,
            "text": "⏳ *Agent is reviewing the contract... please wait.*",
            "thread_ts": thread_ts
        }
        think_res = requests.post("https://slack.com/api/chat.postMessage", headers=headers, json=thinking_payload)
        think_res.raise_for_status()
        
        # Capture the unique timestamp of the thinking message so we can overwrite it later
        thinking_ts = think_res.json().get("ts")


        # --- EXECUTE THE PIPELINE ---
        print("[2/5] Downloading file from Slack...")
        dl_res = requests.get(file_url, headers=headers)
        dl_res.raise_for_status()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(dl_res.content)
            temp_path = temp_file.name

        print("[3/5] Extracting text via Azure Document Intelligence...")
        try:
            parsed = parse_document(temp_path)
            contract_text = parsed.content
            extraction_failed = False
        except ExtractionError as e:
            print(f"Extraction error: {e}")
            extraction_failed = True
        finally:
            os.remove(temp_path)

        if extraction_failed:
            final_report = "❌ *Extraction Failed:* Azure Document Intelligence could not read this file."
            status_header = ""
        else:
            print("[4/5] Running LangGraph Execution with langsmith eval...")
            prompt = f"Please review this vendor contract for compliance violations:\n\n{contract_text}"
            inputs = {"messages": [("user", prompt)]}
            
            # --- THE LANGSMITH CONFIGURATION ---
            # We inject tags and metadata so we can filter traces in the dashboard
            ls_config = RunnableConfig(
                run_name="Vendor_Compliance_Review",
                tags=["production", "slack_trigger"],
                metadata={
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "source": "slack_webhook"
                }
            )
            
            # Pass the config directly into the LangGraph invoke method
            result = app_agent.invoke(inputs, config=ls_config)
            final_report = result["messages"][-1].content
            
            # --- INTELLIGENT ROUTING CHECK ---
            # Check if the AI's scorecard mentions violations or escalations
           # --- INTELLIGENT ROUTING CHECK (Deterministic API Execution) ---
            if "action required" in final_report.lower() or "🚨" in final_report:
                status_header = "🚨 *Compliance Violation Found - Check #legal-escalations*\n\n"
                
                print("[!] Violation detected by Python parser. Executing Webhooks EXACTLY ONCE...")
                
                # Extract the vendor name from the first line of the AI's report
                try:
                    vendor_name = final_report.split("*Compliance Scorecard: ")[1].split("*")[0]
                except IndexError:
                    vendor_name = "Unknown Vendor"

                # --- LOCATE VIOLATIONS FOR HUMAN REVIEW ---
                escalation_text = final_report
                try:
                    review_url = build_review(parsed, dl_res.content, vendor_name, contract_text, final_report)
                    escalation_text = final_report.rstrip() + review_url_footer(review_url[0], review_url[1])
                    final_report = escalation_text
                except Exception as e:
                    # Never block escalation because the review viewer failed
                    print(f"⚠️ Could not build review page: {e}")

                # Fire the tools manually in pure Python
                escalate_to_slack.invoke({
                    "vendor_name": vendor_name,
                    "violation_summary": escalation_text
                })
                create_trello_card.invoke({
                    "vendor_name": vendor_name,
                    "violation_summary": escalation_text
                })
                create_jira_ticket.invoke({
                    "violation_summary": escalation_text
                })
                
            else:
                status_header = "✅ *None Found - Contract meets policies*\n\n"


        # --- UX STATE 2: THE "FINAL" UPDATE ---
        print("[5/5] Overwriting 'Thinking' message with final report...")
        
        # Combine your routing text with the AI's actual scorecard
        ux_final_text = status_header + "*Agent Scorecard:*\n" + final_report

        if thinking_ts:
            # Overwrite the exact message we posted earlier
            update_payload = {
                "channel": channel_id,
                "ts": thinking_ts,
                "text": ux_final_text
            }
            requests.post("https://slack.com/api/chat.update", headers=headers, json=update_payload)
        else:
            # Fallback just in case the timestamp wasn't captured
            fallback_payload = {
                "channel": channel_id,
                "text": ux_final_text,
                "thread_ts": thread_ts
            }
            requests.post("https://slack.com/api/chat.postMessage", headers=headers, json=fallback_payload)
            
        print("✅ Success: Workflow complete.\n")
        if extraction_failed:
            dedup.release(file_id)   # nothing was escalated; allow a retry
        else:
            dedup.mark_done(file_id)

    except Exception as e:
        print(f"❌ Background Processing Error: {str(e)}")
        dedup.release(file_id)   # let a retry or re-delivery of this file run again
        # Gracefully handle the error in Slack so the user isn't stuck waiting forever
        if thinking_ts:
            error_payload = {
                "channel": channel_id,
                "ts": thinking_ts,
                "text": f"❌ *Agent Execution Failed:* {str(e)}"
            }
            requests.post("https://slack.com/api/chat.update", headers=headers, json=error_payload)


@app.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    # 1. URL Verification
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    event = payload.get("event", {})
    
    # 2. FINOPS GUARD CLAUSE: Ignore bot messages
    if event.get("bot_id"):
        return {"status": "ignored", "reason": "Bot message"}

  
    # --- 3. IDEMPOTENCY: every route below claims the file in Redis before doing any work ---
    # Slack retries events and sends several for one upload (`message` + `file_shared`),
    # so all of them are keyed on the same file ID. SET NX EX is atomic across replicas.
    async def claim(file_id):
        if not file_id:
            return True   # nothing to key on; fall through
        if await run_in_threadpool(dedup.claim, file_id):
            return True
        print(f"🛑 Duplicate Slack event ignored. Already handled file: {file_id}")
        return False

    event_type = event.get("type")

    # Handle file_shared events (most reliable way to catch file uploads)
    if event_type == "file_shared":
        file_id = event.get("file_id")
        channel_id = event.get("channel_id")  # note: file_shared uses channel_id not channel

        if file_id and channel_id:
            if not await claim(file_id):
                return {"status": "ignored", "reason": "redundant file event"}

            # Fetch full file info from Slack API
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
            file_info_resp = requests.get(
                "https://slack.com/api/files.info",
                headers=headers,
                params={"file": file_id}
            )
            file_data = file_info_resp.json().get("file", {})
            file_url = file_data.get("url_private_download")
            thread_ts = event.get("event_ts")  # best fallback for file_shared

            if file_url:
                print("File detected via file_shared! Routing to background task...")
                background_tasks.add_task(
                    process_slack_file_background, file_url, channel_id, thread_ts, file_id
                )
                return {"status": "processing"}
            dedup.release(file_id)   # claimed but nothing to process

    # Handle app_mention and message events (with file already embedded)
    if event_type in ["app_mention", "message"]:
        files = event.get("files", [])
        channel_id = event.get("channel") or event.get("channel_id")
        thread_ts = event.get("thread_ts") or event.get("ts")

        # Respond to plain @mentions so users know the bot is alive
        if not files:
            if event_type == "app_mention":
                headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
                requests.post(
                    "https://slack.com/api/chat.postMessage",
                    headers=headers,
                    json={
                        "channel": channel_id,
                        "text": "👋 Hi! Please attach a PDF contract to review.",
                        "thread_ts": thread_ts
                    }
                )
            return {"status": "ignored", "reason": "No file attached"}

        file_id = files[0].get("id")
        file_url = files[0].get("url_private_download")
        if file_url:
            if not await claim(file_id):
                return {"status": "ignored", "reason": "redundant file event"}
            print("File detected via message! Routing to background task...")
            background_tasks.add_task(
                process_slack_file_background, file_url, channel_id, thread_ts, file_id
            )
            return {"status": "processing"}

    return {"status": "ignored"}
