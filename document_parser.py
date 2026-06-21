import os
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient

# Fail Fast: Ensure environment is loaded
if not load_dotenv():
    print("Error: Could not locate or load the .env file.")
    exit(1)

def get_doc_intel_client() -> DocumentIntelligenceClient:
    """Initializes the Azure Document Intelligence Client."""
    endpoint = os.getenv("DOC_INTEL_ENDPOINT")
    key = os.getenv("DOC_INTEL_KEY")
    
    # Guard clause to prevent silent authentication failures
    if not endpoint or not key:
        raise ValueError("Missing DOC_INTEL_ENDPOINT or DOC_INTEL_KEY in .env file.")
        
    return DocumentIntelligenceClient(
        endpoint=endpoint, 
        credential=AzureKeyCredential(key)
    )
#doc intel cannot read .txt
def extract_clean_text(file_path: str) -> str:
    """
    Sends a document to Azure Document Intelligence and extracts ONLY the plain text.
    Discards all complex JSON bounding box and coordinate data to save LLM tokens.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Document not found at path: {file_path}")

    client = get_doc_intel_client()

    print(f"Sending {os.path.basename(file_path)} to Azure Document Intelligence...")
    
    with open(file_path, "rb") as f:
        document_bytes = f.read()

    try:
        # 'prebuilt-read' is faster and cheaper than 'prebuilt-layout' for standard text
        poller = client.begin_analyze_document(
            model_id="prebuilt-read", 
            body=document_bytes,
            content_type="application/octet-stream"
        )
        
        result = poller.result()
        
        # Guard clause: Check if Azure returned empty content
        if not result.content:
            return "Error: Document processed, but no text was found."
            
        # result.content contains the clean, concatenated string of the entire document
        return result.content
        
    except Exception as e:
        return f"Azure Extraction Failed: {str(e)}"

# --- Quick Test ---
if __name__ == "__main__":
    test_file = "vendor_example/VENDOR MASTER SERVICES AGREEMENT (1).pdf"
    
    extracted_text = extract_clean_text(test_file)
    
    print("\n--- Clean Text Extracted for LangGraph ---")
    print(extracted_text)