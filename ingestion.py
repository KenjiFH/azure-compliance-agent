import os
from dotenv import load_dotenv
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import AzureOpenAIEmbeddings
from langchain_community.vectorstores.azuresearch import AzureSearch

# In containers there is no .env file; config comes from real environment variables.
if not load_dotenv():
    print("Warning: .env file not found; relying on process environment variables.")

def get_embedding_model() -> AzureOpenAIEmbeddings:
    """Initializes the Azure OpenAI Embedding model."""
    deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
    
    # Guard clause to prevent obscure API errors during embedding
    if not deployment:
        raise ValueError("Missing AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME in .env")

    return AzureOpenAIEmbeddings(
        azure_deployment=deployment,
        openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION")
    )

def load_documents(directory_path: str):
    """Loads all text files from the specified directory."""
    if not os.path.exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}")
        
    loader = DirectoryLoader(directory_path, glob="**/*.txt", loader_cls=TextLoader)
    return loader.load()

def chunk_documents(documents: list):
    """Splits documents into smaller chunks for the vector store."""
    if not documents:
        raise ValueError("No documents were loaded. Check the data directory.")
        
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    return splitter.split_documents(documents)

def get_vector_store() -> AzureSearch:
    """Connects to the Azure AI Search index holding the policy chunks."""
    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    key = os.getenv("AZURE_SEARCH_KEY")
    index_name = os.getenv("AZURE_SEARCH_INDEX_NAME")

    if not (endpoint and key and index_name):
        raise ValueError("Missing AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY or AZURE_SEARCH_INDEX_NAME")

    return AzureSearch(
        azure_search_endpoint=endpoint,
        azure_search_key=key,
        index_name=index_name,
        embedding_function=get_embedding_model().embed_query,
    )

def initialize_knowledge_base(directory_path: str = "data") -> AzureSearch:
    """Returns the Azure AI Search store. Populate it with ingest_policies.py."""
    return get_vector_store()

# --- Quick Test ---
if __name__ == "__main__":
    try:
        print("Connecting to Azure AI Search...")
        
        # 1. Connect to the index
        vdb = initialize_knowledge_base()
        print("\nSuccess: connected.")
        
        # 2. Verify it works with a semantic similarity search
        test_query = "What is the policy on liability caps?"
        print(f"\nRunning test query: '{test_query}'")
        
        results = vdb.similarity_search(test_query, k=1)
        
        if results:
            print(f"\n--- Test Result Retrieved ---")
            print(results[0].page_content)
        else:
            print("\nTest Failed: No results found.")
            
    except Exception as e:
        print(f"\nIngestion Failed: {str(e)}")