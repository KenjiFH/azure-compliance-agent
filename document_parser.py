import os
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient

from models import Line, Page, ParsedDocument

# In containers there is no .env file; config comes from real environment variables.
if not load_dotenv():
    print("Warning: Could not locate or load the .env file.")

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
class ExtractionError(Exception):
    """Raised when Azure Document Intelligence cannot read the document."""


#doc intel cannot read .txt
def parse_document(file_path: str) -> ParsedDocument:
    """
    Sends a document to Azure Document Intelligence and returns the text PLUS per-line
    page numbers, char offsets and bounding polygons (used to highlight violations).
    Only `content` is sent to the LLM; the geometry stays server-side.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Document not found at path: {file_path}")

    client = get_doc_intel_client()

    print(f"Sending {os.path.basename(file_path)} to Azure Document Intelligence...")

    with open(file_path, "rb") as f:
        document_bytes = f.read()

    try:
        # 'prebuilt-read' is faster and cheaper than 'prebuilt-layout' and still returns lines/polygons
        poller = client.begin_analyze_document(
            model_id="prebuilt-read",
            body=document_bytes,
            content_type="application/octet-stream"
        )
        result = poller.result()
    except Exception as e:
        raise ExtractionError(f"Azure Extraction Failed: {e}") from e

    if not result.content:
        raise ExtractionError("Document processed, but no text was found.")

    pages, lines = [], []
    for p in result.pages or []:
        pages.append(Page(page_number=p.page_number, width=p.width or 0,
                          height=p.height or 0, unit=str(p.unit or "inch")))
        for ln in p.lines or []:
            if not ln.spans:
                continue
            lines.append(Line(page=p.page_number, text=ln.content,
                              offset=ln.spans[0].offset, length=ln.spans[0].length,
                              polygon=list(ln.polygon or [])))

    return ParsedDocument(content=result.content, pages=pages, lines=lines)


def extract_clean_text(file_path: str) -> str:
    """Backwards-compatible: plain text only."""
    return parse_document(file_path).content

# --- Quick Test ---
if __name__ == "__main__":
    test_file = "vendor_example/VENDOR MASTER SERVICES AGREEMENT (1).pdf"

    doc = parse_document(test_file)

    print(f"\n--- {len(doc.pages)} pages, {len(doc.lines)} lines with polygons ---")
    print(doc.content)