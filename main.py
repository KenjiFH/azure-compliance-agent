import os
import requests
import tempfile
from fastapi import FastAPI, BackgroundTasks, Request
from dotenv import load_dotenv
from agent import app_agent
from document_parser import extract_clean_text
import asyncio  # <-- ADDED to address race condidtions
import time    # <-- ADDED to address race condidtions and for garbage collection

from tools import escalate_to_slack, create_trello_card, create_jira_ticket


from langchain_core.runnables.config import RunnableConfig
from langsmith import Client

# Initialize the LangSmith client (useful if we want to programmatically push feedback later)
ls_client = Client()

load_dotenv()

app = FastAPI(title="Logic20/20 Compliance Agent")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# Replace processed_files = set() with a dictionary
processed_files = {}
file_lock = asyncio.Lock()
TTL_SECONDS = 300  # 5 minutes is plenty of time to stop Slack retries

def process_slack_file_background(file_url: str, channel_id: str, thread_ts: str):
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
        contract_text = extract_clean_text(temp_path)
        os.remove(temp_path) 

        if "Error" in contract_text:
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
                
                # Fire the tools manually in pure Python
                escalate_to_slack.invoke({
                    "vendor_name": vendor_name, 
                    "violation_summary": final_report
                })
                create_trello_card.invoke({
                    "vendor_name": vendor_name, 
                    "violation_summary": final_report
                })
                create_jira_ticket.invoke({
                    "violation_summary": final_report
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

    except Exception as e:
        print(f"❌ Background Processing Error: {str(e)}")
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

  
  # --- 3. ASSET-BASED IDEMPOTENCY GUARDRAIL (WITH TTL CACHE) ---
    files = event.get("files", [])
    if files:
        file_id = files[0].get("id")
        current_time = time.time()
        
        async with file_lock:
            # A. GARBAGE COLLECTION: Clean up memory so the server never crashes
            # We use list() to avoid a "dictionary changed size during iteration" error
            for key in list(processed_files.keys()):
                if current_time - processed_files[key] > TTL_SECONDS:
                    del processed_files[key]

            # B. IDEMPOTENCY CHECK
            if file_id in processed_files:
                print(f"🛑 Overlapping Slack event ignored. Already processing file: {file_id}")
                return {"status": "ignored", "reason": "redundant file event"}
                
            # C. REGISTER THE FILE WITH A TIMESTAMP
            processed_files[file_id] = current_time
            
        print("File detected! Instantly returning 200 OK and routing to background task...")
        background_tasks.add_task(process_slack_file_background, file_url, channel_id, thread_ts)
        return {"status": "processing"}

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    event = payload.get("event", {})

    if event.get("bot_id"):
        return {"status": "ignored", "reason": "Bot message"}

    event_type = event.get("type")

    # Handle file_shared events (most reliable way to catch file uploads)
    if event_type == "file_shared":
        file_id = event.get("file_id")
        channel_id = event.get("channel_id")  # note: file_shared uses channel_id not channel

        if file_id and channel_id:
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
                    process_slack_file_background, file_url, channel_id, thread_ts
                )
                return {"status": "processing"}

    # Handle app_mention and message events (with file already embedded)
    if event_type in ["app_mention", "message"]:
        files = event.get("files", [])
        channel_id = event.get("channel")
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

        file_url = files[0].get("url_private_download")
        if file_url:
            print("File detected via message! Routing to background task...")
            background_tasks.add_task(
                process_slack_file_background, file_url, channel_id, thread_ts
            )
            return {"status": "processing"}

    return {"status": "ok"}