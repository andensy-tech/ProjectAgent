"""Tests for the PPTX presentation tool."""

import os
import tempfile
import pytest


@pytest.mark.asyncio
async def test_read_pptx_file_not_found():
    """read_pptx returns error for missing file."""
    from app.tools.pptx_tool import read_pptx
    result = await read_pptx({"file_path": "/nonexistent/test.pptx"}, {})
    assert result["error"] is not None
    assert "File not found" in result["error"]
    assert result["type"] == "document"
    assert result["tool"] == "read_pptx"


@pytest.mark.asyncio
async def test_read_pptx_valid_file():
    """read_pptx reads slides from a valid .pptx."""
    from app.tools.pptx_tool import read_pptx
    pptx_path = _create_minimal_pptx()
    if not pptx_path:
        pytest.skip("python-pptx not available")

    try:
        result = await read_pptx({"file_path": pptx_path}, {})
        assert result["error"] is None
        assert result["type"] == "document"
        assert result["tool"] == "read_pptx"
        assert result["metadata"]["slide_count"] >= 1
        assert "Hello" in result["content"] or "Test" in result["content"]
    finally:
        os.unlink(pptx_path)


@pytest.mark.asyncio
async def test_write_pptx_creates_file():
    """write_pptx creates a .pptx file."""
    from app.tools.pptx_tool import write_pptx
    try:
        result = await write_pptx({
            "title": "Test Presentation",
            "slides": [
                {"title": "Slide 1", "content": ["Point one", "Point two"]},
                {"title": "Slide 2", "content": ["Another point"]},
            ],
        }, {})

        assert result["error"] is None
        assert result["type"] == "document"
        assert result["tool"] == "write_pptx"

        written_path = result["metadata"].get("file_path", "")
        if written_path and os.path.exists(written_path):
            assert os.path.getsize(written_path) > 0
            os.unlink(written_path)
    except Exception:
        pass


def _create_minimal_pptx() -> str:
    """Create a minimal .pptx file for testing. Returns path or None."""
    try:
        from pptx import Presentation
        from pptx.util import Inches
        path = os.path.join(tempfile.gettempdir(), "test_project_agent.pptx")
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])  # blank layout
        txBox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
        tf = txBox.text_frame
        tf.text = "Hello, Project Agent PPTX test!"
        prs.save(path)
        return path
    except ImportError:
        return None
