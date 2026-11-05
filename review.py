"""Human-in-the-loop review endpoints + signed links."""
import base64
import hashlib
import hmac
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import storage

router = APIRouter()
LINK_TTL = 7 * 24 * 3600
STATIC = Path(__file__).parent / "static"


def _secret() -> bytes:
    s = os.getenv("REVIEW_LINK_SECRET")
    if not s:
        raise HTTPException(500, "REVIEW_LINK_SECRET not configured")
    return s.encode()


def _sign(review_id: str, exp: int) -> str:
    mac = hmac.new(_secret(), f"{review_id}.{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_review_url(review_id: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    exp = int(time.time()) + LINK_TTL
    return f"{base}/reviews/{review_id}?exp={exp}&sig={_sign(review_id, exp)}"


def _check(review_id: str, exp: int, sig: str) -> None:
    if exp < time.time() or not hmac.compare_digest(sig, _sign(review_id, exp)):
        raise HTTPException(403, "Invalid or expired review link")


@router.get("/reviews/{review_id}")
def review_page(review_id: str, exp: int = Query(...), sig: str = Query(...)):
    _check(review_id, exp, sig)
    return FileResponse(STATIC / "review.html")


@router.get("/reviews/{review_id}/data")
def review_data(review_id: str, exp: int = Query(...), sig: str = Query(...)):
    _check(review_id, exp, sig)
    return storage.load_review(review_id)


@router.get("/reviews/{review_id}/pdf")
def review_pdf(review_id: str, exp: int = Query(...), sig: str = Query(...)):
    _check(review_id, exp, sig)
    return Response(storage.load_pdf(review_id), media_type="application/pdf")


class Decision(BaseModel):
    status: str  # approved | rejected | pending
    note: str = ""


@router.post("/reviews/{review_id}/findings/{finding_id}")
def decide(review_id: str, finding_id: str, d: Decision,
           exp: int = Query(...), sig: str = Query(...)):
    _check(review_id, exp, sig)
    if d.status not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "bad status")
    review = storage.load_review(review_id)
    for f in review.findings:
        if f.id == finding_id:
            f.status, f.note = d.status, d.note
            storage.save_review(review)
            return f
    raise HTTPException(404, "finding not found")
