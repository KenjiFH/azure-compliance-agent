import os
from dotenv import load_dotenv
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import AzureOpenAIEmbeddings
from langchain_community.vectorstores import FAISS

# Fail Fast: Ensure environment is loaded before any execution
if not load_dotenv():
    print("Error: Could not locate or load the .env file.")
    exit(1)

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

def build_vector_store(chunks: list) -> FAISS:
    """Embeds chunks and builds the in-memory FAISS vector store."""
    embeddings = get_embedding_model()
    return FAISS.from_documents(chunks, embeddings)

def initialize_knowledge_base(directory_path: str = "data") -> FAISS:
    """Orchestrates the data ingestion pipeline."""
    docs = load_documents(directory_path)
    chunks = chunk_documents(docs)
    vector_store = build_vector_store(chunks)
    
    return vector_store

# --- Quick Test ---
if __name__ == "__main__":
    try:
        print("Starting On-Startup Ingestion...")
        
        # 1. Build the database
        vdb = initialize_knowledge_base()
        print("\nSuccess: On-Startup Ingestion complete.")
        
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