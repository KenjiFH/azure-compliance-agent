"""Loads data/*.txt into the Azure AI Search index. Re-run whenever policies change."""
import hashlib
from ingestion import load_documents, chunk_documents, get_vector_store

def main(directory_path: str = "data"):
    chunks = chunk_documents(load_documents(directory_path))
    # Stable IDs make re-runs overwrite instead of duplicating chunks.
    ids = [hashlib.sha1(c.page_content.encode()).hexdigest() for c in chunks]
    get_vector_store().add_documents(chunks, keys=ids)
    print(f"Indexed {len(chunks)} chunks.")

if __name__ == "__main__":
    main()
