"""Tests for the PDF document tool."""

import os
import tempfile
import pytest


@pytest.mark.asyncio
async def test_read_pdf_file_not_found():
    """read_pdf returns error for missing file."""
    from app.tools.pdf_tool import read_pdf
    result = await read_pdf({"file_path": "/nonexistent/test.pdf"}, {})
    assert result["error"] is not None
    assert "File not found" in result["error"]
    assert result["type"] == "document"
    assert result["tool"] == "read_pdf"


@pytest.mark.asyncio
async def test_read_pdf_empty_params():
    """read_pdf returns error when no file_path given."""
    from app.tools.pdf_tool import read_pdf
    result = await read_pdf({}, {})
    assert result["error"] is not None
    assert "File not found" in result["error"]


@pytest.mark.asyncio
async def test_extract_pdf_metadata_file_not_found():
    """extract_pdf_metadata returns error for missing file."""
    from app.tools.pdf_tool import extract_pdf_metadata
    result = await extract_pdf_metadata({"file_path": "/nonexistent/test.pdf"}, {})
    assert result["error"] is not None
    assert "File not found" in result["error"]
    assert result["type"] == "document"
    assert result["tool"] == "extract_pdf_metadata"


@pytest.mark.asyncio
async def test_read_pdf_valid_file():
    """read_pdf extracts text from a valid PDF."""
    from app.tools.pdf_tool import read_pdf
    # Create a minimal valid PDF using fpdf2 or reportlab if available, else skip
    pdf_path = _create_minimal_pdf()
    if not pdf_path:
        pytest.skip("Could not create test PDF (no fpdf2/reportlab)")

    try:
        result = await read_pdf({"file_path": pdf_path}, {})
        assert result["error"] is None
        assert result["type"] == "document"
        assert result["tool"] == "read_pdf"
        assert result["metadata"]["page_count"] >= 1
        assert result["metadata"]["file_name"] == "test.pdf"
        assert len(result["content"]) > 0
    finally:
        os.unlink(pdf_path)


@pytest.mark.asyncio
async def test_read_pdf_page_range():
    """read_pdf supports page_start/page_end parameters."""
    from app.tools.pdf_tool import read_pdf
    pdf_path = _create_minimal_pdf()
    if not pdf_path:
        pytest.skip("Could not create test PDF")

    try:
        result = await read_pdf({"file_path": pdf_path, "page_start": 0, "page_end": 1}, {})
        assert result["error"] is None
        assert result["metadata"]["extracted_pages"] >= 1
    finally:
        os.unlink(pdf_path)


def _create_minimal_pdf() -> str:
    """Create a minimal valid PDF file for testing. Returns path or None."""
    try:
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(text="Hello, Project Agent PDF test!", new_x="LMARGIN", new_y="NEXT")
        path = os.path.join(tempfile.gettempdir(), "test_project_agent.pdf")
        pdf.output(path)
        return path
    except ImportError:
        pass

    try:
        from reportlab.pdfgen import canvas
        path = os.path.join(tempfile.gettempdir(), "test_project_agent.pdf")
        c = canvas.Canvas(path)
        c.drawString(100, 750, "Hello, Project Agent PDF test!")
        c.save()
        return path
    except ImportError:
        pass

    return None
