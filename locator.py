"""Resolve an LLM-provided verbatim quote to page + bounding boxes in the OCR output."""
import re
from typing import List, Optional, Tuple

from rapidfuzz import fuzz

from models import Box, Finding, FindingDraft, ParsedDocument

FUZZY_THRESHOLD = 90  # partial_ratio score (0-100) required to accept an approximate match


def _normalize(text: str) -> Tuple[str, List[int]]:
    """Collapse whitespace and lowercase; return normalized text and a map norm_idx -> original idx."""
    out_chars: List[str] = []
    index_map: List[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_space:
                out_chars.append(" ")
                index_map.append(i)
            prev_space = True
        else:
            out_chars.append(ch.lower())
            index_map.append(i)
            prev_space = False
    if out_chars and out_chars[-1] == " ":
        out_chars.pop()
        index_map.pop()
    return "".join(out_chars), index_map


def _find_span(quote: str, content: str) -> Optional[Tuple[int, int]]:
    norm_content, cmap = _normalize(content)
    norm_quote, _ = _normalize(quote)
    if not norm_quote or not norm_content:
        return None

    pos = norm_content.find(norm_quote)
    if pos != -1:
        return cmap[pos], cmap[pos + len(norm_quote) - 1] + 1

    # Fuzzy fallback: slide a window of the quote's length over the content.
    align = fuzz.partial_ratio_alignment(norm_quote, norm_content)
    if align is not None and align.score >= FUZZY_THRESHOLD and align.dest_end > align.dest_start:
        return cmap[align.dest_start], cmap[align.dest_end - 1] + 1
    return None


def locate(draft: FindingDraft, doc: ParsedDocument, finding_id: str) -> Finding:
    """Turn a FindingDraft into a Finding with page/char range/boxes. located=False if not found."""
    base = draft.model_dump()
    span = _find_span(draft.quote, doc.content)
    if span is None:
        return Finding(id=finding_id, **base)

    start, end = span
    boxes = [
        Box(page=ln.page, polygon=ln.polygon)
        for ln in doc.lines
        if ln.offset < end and ln.offset + ln.length > start
    ]
    if not boxes:
        return Finding(id=finding_id, **base)

    return Finding(
        id=finding_id,
        located=True,
        page=boxes[0].page,
        char_start=start,
        char_end=end,
        boxes=boxes,
        **base,
    )
