import os
import requests
from dotenv import load_dotenv
from langchain_core.tools import tool



from requests.auth import HTTPBasicAuth


# Fail Fast: Load environment variables immediately
if not load_dotenv():
    print("Warning: Could not locate or load the .env file.")

@tool
def calculate_liability_difference(vendor_cap: float, policy_minimum: float) -> str:
    """
    Use this tool to calculate the exact financial risk exposure.
    Pass in the vendor's proposed liability cap and the company's policy minimum.
    """
    difference = policy_minimum - vendor_cap
    if difference > 0:
        return f"Violation: Vendor cap is ${difference:,.2f} under the required company minimum."
    return "Compliant: Vendor meets or exceeds financial requirements."


@tool
def escalate_to_slack(vendor_name: str, violation_summary: str) -> str:
    """
    Escalates a compliance violation to the Slack #legal-escalations channel.
    CRITICAL RULE: You MUST ONLY call this tool EXACTLY ONCE per contract review. 
    If you find multiple violations, you must aggregate them all into a single 
    bulleted list and pass them into the 'violation_summary' argument together.
    DO NOT call this tool multiple times.
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    
    # Guard clause: Prevent silent crashes if environment is misconfigured
    if not webhook_url:
        return "Error: SLACK_WEBHOOK_URL not configured in environment."

    # Slack Block Kit Payload for a professional UI
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚨 Compliance Flag: {vendor_name}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Violation Details:*\n{violation_summary}"
                }
            }
        ]
    }

    try:
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
        return f"Success: Escalated {vendor_name} to Slack."
    except Exception as e:
        return f"Failed to escalate to Slack: {str(e)}"


@tool
def create_trello_card(vendor_name: str, violation_summary: str) -> str:
    """
    Creates a Kanban card on the Trello board for human legal review.
    CRITICAL RULE: Use this ONLY ONCE per contract review. 
    Combine all violations into a single 'violation_summary' string.
    DO NOT call this tool multiple times.
    """
    api_key = os.getenv("TRELLO_API_KEY")
    token = os.getenv("TRELLO_TOKEN")
    list_id = os.getenv("TRELLO_LIST_ID")

    # Guard clause: Fail fast if Trello keys are missing
    if not all([api_key, token, list_id]):
        return "Error: Trello credentials are not fully configured in the environment."

    url = "https://api.trello.com/1/cards"
    
    # Trello relies on query parameters rather than a JSON body for basic card creation
    query = {
        'idList': list_id,
        'key': api_key,
        'token': token,
        'name': f"[ACTION REQUIRED] {vendor_name} Compliance Flag",
        'desc': violation_summary
    }

    try:
        response = requests.post(url, params=query)
        response.raise_for_status()
        
        # Extract the specific card URL to return to the LLM
        card_url = response.json().get("url", "Unknown URL")
        return f"Success: Trello card created at {card_url}"
    except Exception as e:
        return f"Failed to create Trello card: {str(e)}"
    


@tool
def create_jira_ticket(violation_summary: str) -> str:
    """
    Creates a high-priority Jira task for the Engineering/DevOps team 
    when a critical vendor compliance violation is flagged.
    """
    # 1. Pull credentials from your local .env file
    domain = os.getenv("JIRA_DOMAIN")  # e.g., https://your-company.atlassian.net
    email = os.getenv("JIRA_EMAIL")
    api_token = os.getenv("JIRA_API_TOKEN")
    project_key = os.getenv("JIRA_PROJECT_KEY")  # e.g., DEV or COMP

    url = f"{domain}/rest/api/2/issue"
    
    # 2. Authenticate using HTTPBasicAuth
    auth = HTTPBasicAuth(email, api_token)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    # 3. Construct the JSON Payload
    payload = {
        "fields": {
            "project": {
                "key": project_key
            },
            "summary": "🚨 Action Required: Vendor Compliance Violation",
            "description": f"The LangGraph Agent has flagged the following issues that require immediate mitigation:\n\n{violation_summary}",
            "issuetype": {
                "name": "Task" 
            }
        }
    }
    
    # 4. Fire the request
    try:
        response = requests.post(url, json=payload, headers=headers, auth=auth)
        if response.status_code == 201:
            issue_key = response.json().get("key")
            return f"Successfully created Jira ticket: {domain}/browse/{issue_key}"
        else:
            return f"Failed to create ticket. Status: {response.status_code}, Error: {response.text}"
    except Exception as e:
        return f"Error connecting to Jira API: {str(e)}"


# Export the tools as a list so they can be easily bound to the LLM in LangGraph
agent_tools = [calculate_liability_difference, escalate_to_slack, create_trello_card]


# --- API Tests (Run this file directly to verify endpoints) ---
if __name__ == "__main__":
    print("Running Hardcoded API Connectivity Tests...\n")
    
    dummy_vendor = "TestCorp Cloud Services"
    dummy_violation = "This is a hardcoded system test. The vendor's liability cap is only $1M, which violates the $5M minimum policy."

    # 1. Test Slack Webhook
    print("Testing Slack Escalation...")
    # We use .invoke() to test the LangChain wrapper exactly as the LLM will call it
    slack_result = escalate_to_slack.invoke({"vendor_name": dummy_vendor, "violation_summary": dummy_violation})
    print(f"Result: {slack_result}\n")

    # 2. Test Trello API
    print("Testing Trello Board...")
    trello_result = create_trello_card.invoke({"vendor_name": dummy_vendor, "violation_summary": dummy_violation})
    print(f"Result: {trello_result}\n")
    
    print("Check your Slack #legal-escalations channel and your Trello board!")