from locator import locate
from models import FindingDraft, Line, Page, ParsedDocument

TEXT = "Section 9. Liability\nVendor's total liability shall not exceed\n$1,000,000 in aggregate.\nSection 10. Hosting in eu-west-1."
L1, L2, L3, L4 = TEXT.split("\n")


def _doc():
    lines, off = [], 0
    for i, t in enumerate([L1, L2, L3, L4]):
        lines.append(Line(page=1 if i < 3 else 2, text=t, offset=off, length=len(t),
                          polygon=[0, i, 1, i, 1, i + 1, 0, i + 1]))
        off += len(t) + 1
    return ParsedDocument(content=TEXT, pages=[Page(page_number=1, width=8.5, height=11),
                                                Page(page_number=2, width=8.5, height=11)], lines=lines)


def _d(q):
    return FindingDraft(policy_rule="r", severity="high", description="d", quote=q)


def test_exact_single_line():
    f = locate(_d("eu-west-1"), _doc(), "a")
    assert f.located and f.page == 2 and len(f.boxes) == 1


def test_multi_line_quote_spans_lines():
    f = locate(_d("shall not exceed $1,000,000 in aggregate"), _doc(), "a")
    assert f.located and f.page == 1 and len(f.boxes) == 2


def test_whitespace_and_case_drift():
    assert locate(_d("SHALL   NOT exceed\n$1,000,000"), _doc(), "a").located


def test_minor_ocr_drift_fuzzy():
    assert locate(_d("shall not exceed $1,000,00O in aggregate"), _doc(), "a").located


def test_not_found():
    f = locate(_d("termination for convenience upon thirty days notice"), _doc(), "a")
    assert not f.located and f.boxes == []
