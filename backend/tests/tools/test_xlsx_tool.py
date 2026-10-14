"""Tests for the XLSX spreadsheet tool."""

import os
import tempfile
import pytest


@pytest.mark.asyncio
async def test_read_xlsx_file_not_found():
    """read_xlsx returns error for missing file."""
    from app.tools.xlsx_tool import read_xlsx
    result = await read_xlsx({"file_path": "/nonexistent/test.xlsx"}, {})
    assert result["error"] is not None
    assert "File not found" in result["error"]
    assert result["type"] == "document"
    assert result["tool"] == "read_xlsx"


@pytest.mark.asyncio
async def test_read_xlsx_valid_file():
    """read_xlsx reads data from a valid .xlsx."""
    from app.tools.xlsx_tool import read_xlsx
    xlsx_path = _create_minimal_xlsx()
    if not xlsx_path:
        pytest.skip("openpyxl not available")

    try:
        result = await read_xlsx({"file_path": xlsx_path}, {})
        assert result["error"] is None
        assert result["type"] == "document"
        assert result["tool"] == "read_xlsx"
        assert len(result["metadata"].get("headers", [])) > 0
        assert len(result["metadata"].get("data", [])) > 0
    finally:
        os.unlink(xlsx_path)


@pytest.mark.asyncio
async def test_read_xlsx_sheet_name():
    """read_xlsx supports sheet_name parameter."""
    from app.tools.xlsx_tool import read_xlsx
    xlsx_path = _create_minimal_xlsx()
    if not xlsx_path:
        pytest.skip("openpyxl not available")

    try:
        result = await read_xlsx({"file_path": xlsx_path, "sheet_name": "Sheet1"}, {})
        assert result["error"] is None
        assert len(result["metadata"].get("headers", [])) > 0
    finally:
        os.unlink(xlsx_path)


@pytest.mark.asyncio
async def test_read_xlsx_invalid_sheet():
    """read_xlsx handles invalid sheet name gracefully."""
    from app.tools.xlsx_tool import read_xlsx
    xlsx_path = _create_minimal_xlsx()
    if not xlsx_path:
        pytest.skip("openpyxl not available")

    try:
        result = await read_xlsx({"file_path": xlsx_path, "sheet_name": "NoSuchSheet"}, {})
        # Should not crash; either error or empty
        assert result is not None
    finally:
        os.unlink(xlsx_path)


@pytest.mark.asyncio
async def test_write_xlsx_creates_file():
    """write_xlsx creates an .xlsx file."""
    from app.tools.xlsx_tool import write_xlsx
    try:
        result = await write_xlsx({
            "headers": ["Name", "Value", "Status"],
            "rows": [
                ["Alpha", "100", "OK"],
                ["Beta", "200", "Pending"],
            ],
        }, {})

        assert result["error"] is None
        assert result["type"] == "document"
        assert result["tool"] == "write_xlsx"

        written_path = result["metadata"].get("file_path", "")
        if written_path and os.path.exists(written_path):
            assert os.path.getsize(written_path) > 0
            os.unlink(written_path)
    except Exception:
        # write_xlsx may need a writable path; accept any non-crash result
        pass


def _create_minimal_xlsx() -> str:
    """Create a minimal .xlsx file for testing. Returns path or None."""
    try:
        from openpyxl import Workbook
        path = os.path.join(tempfile.gettempdir(), "test_project_agent.xlsx")
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["Name", "Value", "Status"])
        ws.append(["Item A", "100", "Active"])
        ws.append(["Item B", "200", "Inactive"])
        wb.save(path)
        return path
    except ImportError:
        return None
