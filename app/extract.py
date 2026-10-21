"""Turn one file into text pages, tables and suggested metadata. No database access here.

PDF:  PyMuPDF for text, pdfplumber for tables, Tesseract OCR for pages with no text layer.
DOCX: python-docx; the body is one page, each table becomes a table.
XLSX: openpyxl; each sheet is one page and one table.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pymupdf

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# A page whose text layer has fewer characters than this is treated as scanned and OCR'd.
OCR_MIN_CHARS = 25
OCR_DPI = 300
MAX_SHEET_ROWS = 5000


@dataclass
class Page:
    page_no: int
    text: str
    label: str | None = None
    ocr: bool = False
    image_count: int = 0
    drawing_count: int = 0


@dataclass
class Table:
    page_no: int
    table_no: int
    header: list[str]
    rows: list[list[str]]
    extractor: str

    def records(self) -> list[dict[str, str]]:
        return [dict(zip(self.header, row)) for row in self.rows]


@dataclass
class Extracted:
    mime_type: str
    pages: list[Page] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)   # suggestions only
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def report(self) -> dict:
        return {
            "pages": self.page_count,
            "chars": sum(len(p.text) for p in self.pages),
            "ocr_pages": [p.page_no for p in self.pages if p.ocr],
            "empty_pages": [p.page_no for p in self.pages if not p.text.strip()],
            "chart_candidate_pages": [p.page_no for p in self.pages if looks_like_chart(p)],
            "tables": len(self.tables),
            "table_rows": sum(len(t.rows) for t in self.tables),
            "warnings": self.warnings,
        }


def looks_like_chart(page: Page) -> bool:
    return page.drawing_count >= 40 or page.image_count > 0


def extract(path: Path, ocr: bool = True) -> Extracted:
    suffix = path.suffix.lower()
    if suffix not in MIME_TYPES:
        raise ValueError(f"Unsupported file type: {suffix or '(none)'}")
    out = Extracted(mime_type=MIME_TYPES[suffix])
    {".pdf": _pdf, ".docx": _docx, ".xlsx": _xlsx}[suffix](path, out, ocr)
    out.metadata = suggest_metadata(path, out)
    return out


# --- PDF --------------------------------------------------------------------------------------

def _pdf(path: Path, out: Extracted, ocr: bool) -> None:
    import pdfplumber

    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text", sort=True)
            p = Page(page_no=i, text=text,
                     image_count=len(page.get_images(full=False)),
                     drawing_count=len(page.get_drawings()))
            if len(text.strip()) < OCR_MIN_CHARS and p.image_count:
                if ocr:
                    p.text, p.ocr = _ocr_page(page, out, i), True
                else:
                    out.warnings.append(f"page {i}: no text layer, OCR skipped")
            out.pages.append(p)

    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                for n, raw in enumerate(page.extract_tables(), start=1):
                    table = _clean_table(raw, i, n, "pdfplumber")
                    if table:
                        out.tables.append(table)
    except Exception as e:  # a broken table pass must not lose the text
        out.warnings.append(f"table extraction failed: {e}")


def _ocr_page(page, out: Extracted, page_no: int) -> str:
    try:
        import pytesseract
        from PIL import Image

        pix = page.get_pixmap(dpi=OCR_DPI)
        return pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png"))))
    except Exception as e:
        out.warnings.append(f"page {page_no}: OCR failed: {e}")
        return ""


# --- Word -------------------------------------------------------------------------------------

def _docx(path: Path, out: Extracted, ocr: bool) -> None:
    import docx

    d = docx.Document(str(path))
    out.pages.append(Page(page_no=1, text="\n".join(p.text for p in d.paragraphs)))
    for n, t in enumerate(d.tables, start=1):
        table = _clean_table([[c.text for c in row.cells] for row in t.rows], 1, n, "docx")
        if table:
            out.tables.append(table)
    out.warnings.append("Word files have no fixed pages; all text is stored as page 1")


# --- Excel ------------------------------------------------------------------------------------

def _xlsx(path: Path, out: Extracted, ocr: bool) -> None:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for i, ws in enumerate(wb.worksheets, start=1):
            rows = []
            for r, row in enumerate(ws.iter_rows(values_only=True)):
                if r >= MAX_SHEET_ROWS:
                    out.warnings.append(f"sheet '{ws.title}': truncated at {MAX_SHEET_ROWS} rows")
                    break
                rows.append([_cell(v) for v in row])
            table = _clean_table(rows, i, 1, "openpyxl")
            text = "\n".join("\t".join(c for c in row if c) for row in rows if any(row))
            out.pages.append(Page(page_no=i, label=ws.title, text=text))
            if table:
                out.tables.append(table)
    finally:
        wb.close()


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


# --- Tables -----------------------------------------------------------------------------------

def _norm(cell) -> str:
    return re.sub(r"\s+", " ", str(cell)).strip() if cell is not None else ""


def _clean_table(raw, page_no: int, table_no: int, extractor: str) -> Table | None:
    """First non-empty row is the header. Drops empty rows and fully empty columns."""
    rows = [[_norm(c) for c in row] for row in raw if row and any(_norm(c) for c in row)]
    if len(rows) < 2:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [j for j in range(width) if any(r[j] for r in rows)]
    rows = [[r[j] for j in keep] for r in rows]
    if len(keep) < 2:
        return None

    header, seen = [], {}
    for j, name in enumerate(rows[0], start=1):
        name = name or f"column_{j}"
        seen[name] = seen.get(name, 0) + 1
        header.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return Table(page_no=page_no, table_no=table_no, header=header, rows=rows[1:], extractor=extractor)


# --- Metadata suggestions ---------------------------------------------------------------------
# Only ever suggestions: anything the uploader supplies wins, and the database records which
# values were inferred (documents.meta_source) so nobody mistakes a guess for a fact.

KNOWN_ISSUERS = [
    "Singlife", "Manulife", "HSBC Life", "Tokio Marine", "China Taiping", "China Life", "Etiqa",
    "FWD", "Raffles Health", "Allianz", "Transamerica", "Friends Provident", "MSIG", "Sompo",
    "HL Assurance", "AIA", "Prudential", "Great Eastern", "Income",
]

DOCUMENT_TYPES = [
    ("Policy wording", r"policy (wording|contract|conditions)|terms and conditions"),
    ("Product summary", r"product summary"),
    ("Benefit illustration", r"benefit illustration|policy illustration"),
    ("Brochure", r"brochure"),
    ("Fact sheet", r"fact ?sheet"),
    ("Underwriting guide", r"underwriting guide"),
    ("Circular", r"\bcircular\b|notice to (advisers|representatives)"),
    ("Premium table", r"premium (rates?|table)"),
]

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_DATE = rf"(\d{{1,2}}\s+(?:{_MONTHS})[a-z]*\.?\s+\d{{4}}|(?:{_MONTHS})[a-z]*\.?\s+\d{{1,2}},?\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})"
_EFFECTIVE = re.compile(rf"(?:effective|w\.e\.f\.?|with effect from)\s*(?:date|from|on)?\s*[:\-]?\s*{_DATE}", re.I)
_VERSION = re.compile(r"\b(?:version|ver\.?|v)\s*[:\-]?\s*(\d+(?:\.\d+)*)\b", re.I)


def parse_date(s: str) -> date | None:
    s = s.strip().rstrip(".").replace(",", "")
    s = re.sub(r"\bsept\b", "sep", s, flags=re.I)
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b. %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def suggest_metadata(path: Path, ex: Extracted) -> dict[str, object]:
    head = "\n".join(p.text for p in ex.pages[:3])
    lower = head.lower()
    found: dict[str, object] = {}

    for issuer in KNOWN_ISSUERS:
        if re.search(rf"\b{re.escape(issuer)}\b", head, re.I):
            found["issuer"] = issuer
            break
    for name, pattern in DOCUMENT_TYPES:
        if re.search(pattern, lower):
            found["document_type"] = name
            break
    if m := _EFFECTIVE.search(head):
        if d := parse_date(m.group(1)):
            found["effective_date"] = d
    if m := _VERSION.search(head):
        found["version"] = m.group(1)
    return found
