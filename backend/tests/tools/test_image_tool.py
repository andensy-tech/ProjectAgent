"""Tests for the image processing tools."""

import os
import tempfile
import pytest


@pytest.mark.asyncio
async def test_read_image_metadata_file_not_found():
    """read_image_metadata returns error for missing file."""
    from app.tools.image_tool import read_image_metadata
    result = await read_image_metadata({"file_path": "/nonexistent/test.png"}, {})
    assert result["error"] is not None
    assert "File not found" in result["error"]
    assert result["type"] == "document"
    assert result["tool"] == "read_image_metadata"


@pytest.mark.asyncio
async def test_read_image_metadata_invalid_file():
    """read_image_metadata handles garbage file."""
    from app.tools.image_tool import read_image_metadata
    path = os.path.join(tempfile.gettempdir(), "fake_img.png")
    with open(path, "wb") as f:
        f.write(b"this is not a real image file")
    try:
        result = await read_image_metadata({"file_path": path}, {})
        assert result["error"] is not None  # Cannot identify image file
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_read_image_metadata_valid_png():
    """read_image_metadata extracts metadata from a real PNG."""
    from app.tools.image_tool import read_image_metadata
    img_path = _create_test_png()
    if not img_path:
        pytest.skip("Pillow not available")

    try:
        result = await read_image_metadata({"file_path": img_path}, {})
        assert result["error"] is None
        assert result["type"] == "document"
        assert result["tool"] == "read_image_metadata"
        assert result["metadata"]["format"] == "PNG"
        assert result["metadata"]["width"] > 0
        assert result["metadata"]["height"] > 0
    finally:
        os.unlink(img_path)


@pytest.mark.asyncio
async def test_ocr_image_no_tesseract():
    """ocr_image gracefully handles missing tesseract."""
    from app.tools.image_tool import ocr_image
    img_path = _create_test_png()
    if not img_path:
        pytest.skip("Pillow not available")

    try:
        result = await ocr_image({"file_path": img_path, "lang": "eng"}, {})
        # tesseract may or may not be installed; either way should not crash
        assert result["type"] == "document"
        assert result["tool"] == "ocr_image"
    finally:
        os.unlink(img_path)


def _create_test_png() -> str:
    """Create a minimal valid PNG for testing. Returns path or None."""
    try:
        from PIL import Image
        path = os.path.join(tempfile.gettempdir(), "test_project_agent.png")
        img = Image.new("RGB", (100, 50), color="white")
        img.save(path)
        return path
    except ImportError:
        return None
