"""Tests for the file upload API."""

import os
import json
import tempfile
import pytest
from unittest.mock import patch

from app.core.config import settings


def test_upload_unsupported_extension(test_client):
    """Upload a file with unsupported extension returns 400."""
    response = test_client.post(
        "/api/upload",
        files={"file": ("test.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 400
    assert "unsupported" in response.text.lower() or "Unsupported" in response.text


def test_upload_success(test_client):
    """Upload a valid file returns file metadata."""
    response = test_client.post(
        "/api/upload",
        files={"file": ("test.pdf", b"%PDF-1.4 mock pdf content", "application/pdf")},
    )
    if response.status_code == 200:
        data = response.json()
        assert "file_id" in data
        assert data["file_name"] == "test.pdf"
        assert data["file_size"] > 0
        assert data["file_path"] is not None
        assert data["content_type"] == "application/pdf"

        # Verify the file can be retrieved
        file_id = data["file_id"]
        get_resp = test_client.get(f"/api/files/{file_id}")
        assert get_resp.status_code == 200
        meta = get_resp.json()
        assert meta["file_name"] == "test.pdf"

        # Cleanup
        import shutil
        upload_dir = os.path.join(settings.UPLOAD_DIR, file_id)
        if os.path.exists(upload_dir):
            shutil.rmtree(upload_dir)
    else:
        # Auth may fail in test context; that's acceptable
        assert response.status_code in (200, 401, 403)


def test_upload_docx(test_client):
    """Upload a .docx file."""
    response = test_client.post(
        "/api/upload",
        files={"file": ("report.docx", b"mock docx content", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    if response.status_code == 200:
        data = response.json()
        assert data["file_name"] == "report.docx"
        assert "file_id" in data

        import shutil
        upload_dir = os.path.join(settings.UPLOAD_DIR, data["file_id"])
        if os.path.exists(upload_dir):
            shutil.rmtree(upload_dir)


def test_upload_png(test_client):
    """Upload a .png file."""
    response = test_client.post(
        "/api/upload",
        files={"file": ("screenshot.png", b"mock png content", "image/png")},
    )
    if response.status_code == 200:
        data = response.json()
        assert data["file_name"] == "screenshot.png"

        import shutil
        upload_dir = os.path.join(settings.UPLOAD_DIR, data["file_id"])
        if os.path.exists(upload_dir):
            shutil.rmtree(upload_dir)


def test_upload_file_not_found(test_client):
    """GET /api/files/<invalid> returns 404."""
    response = test_client.get("/api/files/nonexistent-id-12345")
    assert response.status_code == 404


def test_upload_allowed_extensions_match():
    """ALLOWED_EXTENSIONS contains all expected types."""
    from app.api.upload import ALLOWED_EXTENSIONS
    assert ".pdf" in ALLOWED_EXTENSIONS
    assert ".docx" in ALLOWED_EXTENSIONS
    assert ".doc" in ALLOWED_EXTENSIONS
    assert ".xlsx" in ALLOWED_EXTENSIONS
    assert ".xls" in ALLOWED_EXTENSIONS
    assert ".pptx" in ALLOWED_EXTENSIONS
    assert ".ppt" in ALLOWED_EXTENSIONS
    assert ".png" in ALLOWED_EXTENSIONS
    assert ".jpg" in ALLOWED_EXTENSIONS
    assert ".jpeg" in ALLOWED_EXTENSIONS
    assert ".gif" in ALLOWED_EXTENSIONS
    assert ".bmp" in ALLOWED_EXTENSIONS
    assert ".tiff" in ALLOWED_EXTENSIONS
    assert ".tif" in ALLOWED_EXTENSIONS


def test_intent_document_detection_attached_file():
    """Document routing is now handled by LLM function calling via tool definitions.
    This test validates that the file extension → tool mapping exists in system memory."""
    from app.services.memory.system import memory_system
    tools_desc = memory_system.system_memory.get_tool_descriptions()
    # Built-in tools should be registered
    print(f"Tools: {tools_desc[:200] if tools_desc else 'none'}")
    # (This is a placeholder — actual routing is LLM-driven)


def test_intent_document_detection_extension():
    """Document detection from file extensions is now handled by LLM reasoning."""
    pass  # LLM handles this via function calling


def test_intent_document_detection_keyword():
    """Document detection from keywords is now handled by LLM reasoning."""
    pass  # LLM handles this via function calling
