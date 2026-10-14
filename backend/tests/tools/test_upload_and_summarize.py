"""
Integration tests: simulate file upload → read → LLM summarize for each file type.

Each test reads a real file from the temp/ directory, calls the appropriate
document tool, simulates an LLM summarization call, and writes results to
temp/summarize_results.txt for inspection.
"""

import os
import json
import pytest

TEMP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "temp")
OUTPUT_FILE = os.path.join(TEMP_DIR, "summarize_results.txt")

# File paths under test
TEST_FILES = {
    "doc": os.path.join(TEMP_DIR, "Android GMS测试说明环境搭建V1.1.doc"),
    "pdf": os.path.join(TEMP_DIR, "Fetching Android Build Artifacts - Android Partners.pdf"),
    "pptx": os.path.join(TEMP_DIR, "kickoff-meeting-sample.pptx"),
    "png": os.path.join(TEMP_DIR, "schedule-ch.png"),
    "xlsx": os.path.join(TEMP_DIR, "CHIP-X与9216L部分性能指标对比.xlsx"),
}

RESULTS = []  # Collect results, flush to file at the end


def _simulate_llm_summary(file_name: str, tool: str, content: str, metadata: dict) -> str:
    """Simulate LLM summarization (no actual API call)."""
    lines = []
    lines.append(f"【{file_name}】文档总结")
    lines.append(f"工具: {tool}")
    lines.append(f"文件大小: {metadata.get('file_size', 0)} bytes")
    lines.append(f"")

    # Build a mock summary based on content characteristics
    char_count = len(content)
    word_count_est = len(content.split())
    lines.append(f"文档字数: ~{word_count_est} 词 / {char_count} 字符")
    lines.append(f"")

    if tool == "read_pdf":
        lines.append(f"总页数: {metadata.get('page_count', '?')} 页")
    elif tool == "read_pptx":
        slides = metadata.get("slides", [])
        lines.append(f"总页数: {metadata.get('slide_count', 0)} 页")
        if slides:
            lines.append(f"幻灯片标题: {slides[0].get('texts', [''])[:3]}")
    elif tool == "read_xlsx":
        lines.append(f"行数: {metadata.get('rows', 0)}, 列数: {metadata.get('columns', 0)}")
        headers = metadata.get("headers", [])
        if headers:
            lines.append(f"表头: {', '.join(headers)}")
    elif tool == "read_image_metadata":
        lines.append(f"图片格式: {metadata.get('format', '?')}")
        lines.append(f"尺寸: {metadata.get('width', '?')}x{metadata.get('height', '?')}")

    lines.append(f"")

    # Extract first meaningful line as "summary"
    preview = content.strip()[:300].replace("\n", " ")
    lines.append(f"[模拟总结] 该文档主要内容: {preview}...")

    if tool in ("read_pdf", "read_docx", "read_pptx"):
        lines.append(f"[模拟总结] 建议: 该文档可能包含技术细节，建议详细阅读全文。")

    return "\n".join(lines)


def _append_result(file_name: str, status: str, tool: str, content_preview: str,
                   metadata: dict, llm_summary: str = "", error: str = ""):
    """Append a formatted result block."""
    RESULTS.append(f"""
{'=' * 60}
文件: {file_name}
类型: {os.path.splitext(file_name)[1]}
工具: {tool}
状态: {status}
{'=' * 60}
""")
    if error:
        RESULTS.append(f"错误: {error}\n")
    if content_preview:
        RESULTS.append(f"提取内容预览 ({len(content_preview)} 字符):\n{content_preview}\n")
    if llm_summary:
        RESULTS.append(f"LLM 总结:\n{llm_summary}\n")


def _flush_results():
    """Write all collected results to the output file."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(f"文档解析与总结测试结果\n{'=' * 60}\n")
        f.write(f"时间: 2026-05-27\n")
        f.write(f"文件数: {len([r for r in RESULTS if '=' in r])}\n\n")
        f.write("".join(RESULTS))
    print(f"\n结果已写入: {OUTPUT_FILE}")


# ──────────────────────────────────────────────
# PDF
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pdf_upload_and_summarize():
    """Read PDF -> simulate LLM summary -> write results."""
    from app.tools.pdf_tool import read_pdf

    result = await read_pdf({"file_path": TEST_FILES["pdf"]}, {})
    assert result["error"] is None, f"PDF read failed: {result['error']}"
    assert result["type"] == "document"

    content = result.get("content", "")
    meta = result.get("metadata", {})
    llm_out = _simulate_llm_summary(
        os.path.basename(TEST_FILES["pdf"]), "read_pdf",
        content, meta,
    )
    _append_result(
        os.path.basename(TEST_FILES["pdf"]), "SUCCESS", "read_pdf",
        content[:500], meta, llm_summary=llm_out,
    )
    _flush_results()


# ──────────────────────────────────────────────
# PPTX
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pptx_upload_and_summarize():
    """Read PPTX -> simulate LLM summary -> write results."""
    from app.tools.pptx_tool import read_pptx

    result = await read_pptx({"file_path": TEST_FILES["pptx"]}, {})
    assert result["error"] is None, f"PPTX read failed: {result['error']}"
    assert result["type"] == "document"

    content = result.get("content", "")
    meta = result.get("metadata", {})
    llm_out = _simulate_llm_summary(
        os.path.basename(TEST_FILES["pptx"]), "read_pptx",
        content, meta,
    )
    _append_result(
        os.path.basename(TEST_FILES["pptx"]), "SUCCESS", "read_pptx",
        content[:500], meta, llm_summary=llm_out,
    )
    _flush_results()


# ──────────────────────────────────────────────
# XLSX
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_xlsx_upload_and_summarize():
    """Read XLSX -> simulate LLM summary -> write results."""
    from app.tools.xlsx_tool import read_xlsx

    result = await read_xlsx({"file_path": TEST_FILES["xlsx"]}, {})
    assert result["error"] is None, f"XLSX read failed: {result['error']}"
    assert result["type"] == "document"

    content = result.get("content", "")
    meta = result.get("metadata", {})
    llm_out = _simulate_llm_summary(
        os.path.basename(TEST_FILES["xlsx"]), "read_xlsx",
        content, meta,
    )
    _append_result(
        os.path.basename(TEST_FILES["xlsx"]), "SUCCESS", "read_xlsx",
        content[:500], meta, llm_summary=llm_out,
    )
    _flush_results()


# ──────────────────────────────────────────────
# PNG (image metadata + OCR)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_png_upload_and_summarize():
    """Read PNG metadata (OCR unavailable without tesseract binary) -> write results."""
    from app.tools.image_tool import read_image_metadata, ocr_image

    # Test 1: metadata extraction
    meta_result = await read_image_metadata({"file_path": TEST_FILES["png"]}, {})
    assert meta_result["error"] is None, f"Image metadata failed: {meta_result['error']}"
    assert meta_result["type"] == "document"

    content = meta_result.get("content", "")
    meta = meta_result.get("metadata", {})

    # Test 2: OCR (will gracefully fail without tesseract binary)
    ocr_result = await ocr_image({"file_path": TEST_FILES["png"], "lang": "chi_sim+eng"}, {})
    ocr_text = ""
    if ocr_result["error"]:
        ocr_text = f"[OCR 不可用] {ocr_result['error']}"
    else:
        ocr_text = ocr_result.get("content", "")

    # Build combined response for LLM simulation
    combined = content + "\n" + (ocr_text or "")
    llm_out = _simulate_llm_summary(
        os.path.basename(TEST_FILES["png"]), "read_image_metadata+ocr",
        combined, meta,
    )
    if ocr_text:
        llm_out += f"\n[OCR 结果]\n{ocr_text[:300]}"

    _append_result(
        os.path.basename(TEST_FILES["png"]), "SUCCESS", "read_image_metadata+ocr",
        combined[:500], meta, llm_summary=llm_out,
    )
    _flush_results()


# ──────────────────────────────────────────────
# DOC (old format, unsupported)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doc_upload_and_summarize():
    """Old .doc format is unsupported — verify graceful error and document limitation."""
    from app.tools.docx_tool import read_docx

    result = await read_docx({"file_path": TEST_FILES["doc"]}, {})

    # python-docx cannot read old .doc format
    assert result["type"] == "document"
    assert result["tool"] == "read_docx"

    # Must either error (unsupported format) or content available
    content = result.get("content", "")
    meta = result.get("metadata", {})
    error = result.get("error", "")

    if error:
        status = "UNSUPPORTED (旧版 .doc 格式)"
    else:
        status = "SUCCESS"

    llm_out = ""
    if content:
        llm_out = _simulate_llm_summary(
            os.path.basename(TEST_FILES["doc"]), "read_docx",
            content, meta,
        )

    _append_result(
        os.path.basename(TEST_FILES["doc"]), status, "read_docx",
        (content or "")[:500], meta, llm_summary=llm_out, error=error,
    )
    _flush_results()
