import shutil
from datetime import date

import pytest

from app.extract import _clean_table, extract, parse_date
from tests.fixtures import policy_docx, policy_pdf, rates_xlsx, scanned_pdf


def test_pdf_text_tables_and_metadata(tmp_path):
    ex = extract(policy_pdf(tmp_path / "summary.pdf"))
    assert ex.page_count == 2
    assert "hospitalisation" in ex.pages[0].text
    assert "Pre-existing" in ex.pages[1].text
    assert not any(p.ocr for p in ex.pages)

    assert len(ex.tables) == 1
    t = ex.tables[0]
    assert t.page_no == 1
    assert t.header == ["Age band", "Annual premium (S$)", "Sum assured (S$)"]
    assert t.rows[1] == ["31 - 45", "540", "100,000"]
    assert t.records()[2]["Annual premium (S$)"] == "1,180"

    assert ex.metadata == {"issuer": "Singlife", "document_type": "Product summary",
                           "effective_date": date(2026, 4, 1), "version": "3"}


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_scanned_pdf_is_ocrd(tmp_path):
    ex = extract(scanned_pdf(tmp_path / "scan.pdf", "Waiting period 90 days"))
    assert ex.pages[0].ocr
    assert "Waiting period" in ex.pages[0].text


def test_scanned_pdf_without_ocr_warns(tmp_path):
    ex = extract(scanned_pdf(tmp_path / "scan.pdf", "Waiting period 90 days"), ocr=False)
    assert not ex.pages[0].text.strip()
    assert "OCR skipped" in ex.warnings[0]


def test_docx(tmp_path):
    ex = extract(policy_docx(tmp_path / "b.docx"))
    assert "Critical illness" in ex.pages[0].text
    assert ex.tables[0].records() == [{"Benefit": "Early stage", "Payout": "25%"},
                                      {"Benefit": "Major stage", "Payout": "100%"}]
    assert ex.metadata["issuer"] == "Manulife"
    assert ex.metadata["document_type"] == "Brochure"


def test_xlsx(tmp_path):
    ex = extract(rates_xlsx(tmp_path / "r.xlsx"))
    assert ex.pages[0].label == "Rates"
    assert ex.tables[0].header == ["Plan", "Term", "Rate"]
    assert len(ex.tables[0].rows) == 2


def test_unsupported_type(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("x")
    with pytest.raises(ValueError):
        extract(p)


def test_clean_table_handles_messy_input():
    t = _clean_table([[None, None, None], ["Name", "", "Name"], ["a", None, "b"], ["", "", ""]], 1, 1, "x")
    assert t.header == ["Name", "Name_2"]      # empty column dropped, duplicate header renamed
    assert t.rows == [["a", "b"]]
    assert _clean_table([["only one row"]], 1, 1, "x") is None


@pytest.mark.parametrize("s,expected", [
    ("1 April 2026", date(2026, 4, 1)), ("1 Apr 2026", date(2026, 4, 1)), ("April 1, 2026", date(2026, 4, 1)),
    ("2026-04-01", date(2026, 4, 1)), ("01/04/2026", date(2026, 4, 1)), ("1 Sept 2026", date(2026, 9, 1)),
    ("not a date", None),
])
def test_parse_date(s, expected):
    assert parse_date(s) == expected
