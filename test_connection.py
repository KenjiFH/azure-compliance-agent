import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage

# Early return/Fail Fast: Ensure the environment file loads before executing logic
if not load_dotenv():
    print("Error: Could not locate or load the .env file.")
    exit(1)

def test_azure_connection() -> None:
    """
    Tests the connection to Azure OpenAI using configured environment variables.
    Fails fast if required variables are missing.
    """
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")

    # Guard clause: Verify all variables exist to prevent obscure API errors
    if not all([api_key, endpoint, deployment, api_version]):
        print("Error: Missing required Azure OpenAI environment variables in .env file.")
        return

    try:
        print(f"Attempting secure handshake with Azure OpenAI at: {endpoint}")
        
        # Initialize the Azure LangChain wrapper
        llm = AzureChatOpenAI(
            azure_deployment=deployment,
            api_version=api_version,
            temperature=0.0
        )
        
        # Test the connection with a deterministic instruction
        message = HumanMessage(content="Respond with the exact phrase: 'Connection Successful'")
        response = llm.invoke([message])
        
        print("\n--- Result ---")
        print(response.content)
        
    except Exception as e:
        print(f"\nConnection Failed. Error details:\n{str(e)}")

if __name__ == "__main__":
    test_azure_connection()