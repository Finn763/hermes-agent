from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_context(tmp_path: Path):
    from agent.workspace_types import WorkspacePluginContext

    return WorkspacePluginContext(
        hermes_home=str(tmp_path),
        workspace_root=str(tmp_path),
        knowledgebase_root=str(tmp_path / "kb"),
    )


# -----------------------------------------------------------------------
# PDF parser tests
# -----------------------------------------------------------------------


def test_pdf_parser_extracts_text(tmp_path):
    """Mock fitz to return pages with text, verify WorkspaceDocument output."""
    from plugins.workspace.parsers.pdf import PdfParser

    parser = PdfParser()
    ctx = _make_context(tmp_path)

    # Build a fake fitz module
    fake_page_1 = MagicMock()
    fake_page_1.get_text.return_value = "Hello from page one."
    fake_page_2 = MagicMock()
    fake_page_2.get_text.return_value = "Goodbye from page two."

    fake_doc = MagicMock()
    fake_doc.__iter__ = MagicMock(return_value=iter([fake_page_1, fake_page_2]))
    fake_doc.close = MagicMock()

    fake_fitz = types.ModuleType("fitz")
    fake_fitz.open = MagicMock(return_value=fake_doc)

    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-fake")

    with patch.dict(sys.modules, {"fitz": fake_fitz}):
        result = parser.parse(pdf_path, config={}, context=ctx)

    assert result is not None
    assert result.media_type == "application/pdf"
    assert "--- Page 1 ---" in result.text
    assert "Hello from page one." in result.text
    assert "--- Page 2 ---" in result.text
    assert "Goodbye from page two." in result.text
    assert result.source_path == str(pdf_path)


def test_pdf_parser_fallback_to_pdfplumber(tmp_path):
    """When fitz is not available, parser falls back to pdfplumber."""
    from plugins.workspace.parsers.pdf import PdfParser

    parser = PdfParser()
    ctx = _make_context(tmp_path)

    fake_page_1 = MagicMock()
    fake_page_1.extract_text.return_value = "Plumber page one."
    fake_page_2 = MagicMock()
    fake_page_2.extract_text.return_value = "Plumber page two."

    fake_pdf = MagicMock()
    fake_pdf.pages = [fake_page_1, fake_page_2]
    fake_pdf.close = MagicMock()

    fake_pdfplumber = types.ModuleType("pdfplumber")
    fake_pdfplumber.open = MagicMock(return_value=fake_pdf)

    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-fake")

    # Remove fitz from modules so the import fails, add pdfplumber
    saved_fitz = sys.modules.pop("fitz", None)
    try:
        with patch.dict(sys.modules, {"fitz": None, "pdfplumber": fake_pdfplumber}):
            result = parser.parse(pdf_path, config={}, context=ctx)
    finally:
        if saved_fitz is not None:
            sys.modules["fitz"] = saved_fitz

    assert result is not None
    assert result.media_type == "application/pdf"
    assert "--- Page 1 ---" in result.text
    assert "Plumber page one." in result.text
    assert "--- Page 2 ---" in result.text
    assert "Plumber page two." in result.text


def test_pdf_parser_not_available_without_deps(tmp_path):
    """When neither fitz nor pdfplumber is installed, is_available returns False."""
    from plugins.workspace.parsers.pdf import PdfParser

    parser = PdfParser()
    ctx = _make_context(tmp_path)

    saved_fitz = sys.modules.pop("fitz", None)
    saved_plumber = sys.modules.pop("pdfplumber", None)
    try:
        with patch.dict(sys.modules, {"fitz": None, "pdfplumber": None}):
            assert parser.is_available({}, ctx) is False
    finally:
        if saved_fitz is not None:
            sys.modules["fitz"] = saved_fitz
        if saved_plumber is not None:
            sys.modules["pdfplumber"] = saved_plumber


# -----------------------------------------------------------------------
# DOCX parser tests
# -----------------------------------------------------------------------


def test_docx_parser_extracts_paragraphs(tmp_path):
    """Mock docx.Document to return paragraphs, verify output."""
    from plugins.workspace.parsers.docx import DocxParser

    parser = DocxParser()
    ctx = _make_context(tmp_path)

    para_1 = MagicMock()
    para_1.text = "First paragraph."
    para_2 = MagicMock()
    para_2.text = "Second paragraph."
    para_3 = MagicMock()
    para_3.text = "  "  # whitespace-only, should be skipped

    fake_doc_instance = MagicMock()
    fake_doc_instance.paragraphs = [para_1, para_2, para_3]

    fake_docx = types.ModuleType("docx")
    fake_docx.Document = MagicMock(return_value=fake_doc_instance)

    docx_path = tmp_path / "test.docx"
    docx_path.write_bytes(b"PK-fake")

    with patch.dict(sys.modules, {"docx": fake_docx}):
        result = parser.parse(docx_path, config={}, context=ctx)

    assert result is not None
    assert result.media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert "First paragraph." in result.text
    assert "Second paragraph." in result.text
    # whitespace-only paragraph should not appear
    assert result.text.strip().count("\n\n") == 1  # exactly one separator
    assert result.source_path == str(docx_path)


def test_docx_parser_not_available_without_deps(tmp_path):
    """When python-docx is not installed, is_available returns False."""
    from plugins.workspace.parsers.docx import DocxParser

    parser = DocxParser()
    ctx = _make_context(tmp_path)

    saved_docx = sys.modules.pop("docx", None)
    try:
        with patch.dict(sys.modules, {"docx": None}):
            assert parser.is_available({}, ctx) is False
    finally:
        if saved_docx is not None:
            sys.modules["docx"] = saved_docx
