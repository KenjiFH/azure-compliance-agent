"""Azure Blob persistence for the original PDF and the review JSON.

Auth: AZURE_STORAGE_CONNECTION_STRING (local/Azurite) or AZURE_STORAGE_ACCOUNT_URL + managed identity.
"""
import os
from functools import lru_cache

from azure.storage.blob import BlobServiceClient

from models import Review

CONTAINER = os.getenv("REVIEW_CONTAINER", "reviews")


@lru_cache(maxsize=1)
def _container():
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if conn:
        svc = BlobServiceClient.from_connection_string(conn)
    else:
        url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        if not url:
            raise ValueError("Set AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_URL")
        from azure.identity import DefaultAzureCredential
        svc = BlobServiceClient(account_url=url, credential=DefaultAzureCredential())
    c = svc.get_container_client(CONTAINER)
    if not c.exists():
        c.create_container()
    return c


def save_pdf(review_id: str, data: bytes) -> None:
    _container().upload_blob(f"{review_id}/contract.pdf", data, overwrite=True)


def load_pdf(review_id: str) -> bytes:
    return _container().download_blob(f"{review_id}/contract.pdf").readall()


def save_review(review: Review) -> None:
    _container().upload_blob(f"{review.id}/review.json", review.model_dump_json(), overwrite=True)


def load_review(review_id: str) -> Review:
    raw = _container().download_blob(f"{review_id}/review.json").readall()
    return Review.model_validate_json(raw)
