from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Line(BaseModel):
    """One OCR line with its position in the flat text and on the page."""
    page: int
    text: str
    offset: int          # char offset into ParsedDocument.content
    length: int
    polygon: List[float]  # [x1,y1,...,x4,y4] in page units (inches for PDFs)


class Page(BaseModel):
    page_number: int
    width: float
    height: float
    unit: str = "inch"


class ParsedDocument(BaseModel):
    content: str
    pages: List[Page]
    lines: List[Line]


class Box(BaseModel):
    page: int
    polygon: List[float]


class FindingDraft(BaseModel):
    """What the LLM produces. Deliberately has no coordinates."""
    policy_rule: str = Field(description="Short name of the violated policy rule")
    severity: Literal["low", "medium", "high"]
    description: str = Field(description="One sentence explaining the violation")
    quote: str = Field(
        description="EXACT verbatim text copied from the contract that violates the rule. "
        "Do not paraphrase or fix typos. Keep it to the shortest clause that proves the violation."
    )


class FindingList(BaseModel):
    findings: List[FindingDraft]


class Finding(FindingDraft):
    id: str
    located: bool = False
    page: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    boxes: List[Box] = []
    status: Literal["pending", "approved", "rejected"] = "pending"
    note: str = ""


class Review(BaseModel):
    id: str
    vendor: str
    filename: str = "contract.pdf"
    pages: List[Page] = []
    findings: List[Finding] = []
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
