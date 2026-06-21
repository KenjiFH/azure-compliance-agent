import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent
from langchain.tools.retriever import create_retriever_tool
from tools import calculate_liability_difference
from ingestion import initialize_knowledge_base

if not load_dotenv():
    print("Warning: Could not locate or load the .env file.")

def build_agent_executor():
    """
    Compiles the tools, vector DB, and Azure OpenAI into a LangGraph agent.
    """
    print("Initializing Agent Executor with Advanced Formatting Rules...")

    llm = AzureChatOpenAI(
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        temperature=0.0 # Deterministic output
    )

    vdb = initialize_knowledge_base("data")
    retriever = vdb.as_retriever(search_kwargs={"k": 3})
    
    retriever_tool = create_retriever_tool(
        retriever,
        "company_policy_retriever",
        "Searches the master company security and compliance policies. ALWAYS use this to check rules before making a decision."
    )

    # 1. Give it ONLY the policy retriever and the math calculator
    all_tools = [calculate_liability_difference, retriever_tool]

   # 2. Update the prompt to remove mentions of the escalation tools
    system_prompt = SystemMessage(
        content="""You are an elite Enterprise Compliance AI Agent reviewing vendor contracts.
        
        CRITICAL EXECUTION RULES:
        1. Read the contract and use `company_policy_retriever` to find all applicable rules.
        2. Use `calculate_liability_difference` to verify financial compliance.
        3. Combine all violations into a SINGLE concise summary string.

        FINAL OUTPUT FORMATTING RULES:
        You must format your final response to the user EXACTLY like this using Slack Markdown:

        *Compliance Scorecard: [Insert Vendor Name]*
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        > *Overall Status:* [✅ Compliant / 🚨 Action Required]

        *📊 Financial Review:*
        • [Bullet point summary]

        *🔒 Security & Data Residency:*
        • [Bullet point summary]
        """
    )

    agent_executor = create_react_agent(llm, all_tools, state_modifier=system_prompt)
    
    return agent_executor

app_agent = build_agent_executor()