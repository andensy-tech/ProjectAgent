"""
PDF tool — extract text and metadata from PDF files using pdfplumber.

Supports two extraction modes:
1. Text extraction via pdfplumber (selectable text)
2. Image/OCR extraction via rendering each page to an image and running OCR
   (captures content in embedded screenshots that have no selectable text)

When enable_ocr=True, both text and OCR results are combined in reading order
with clear section markers.
"""

import os
import tempfile
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


async def read_pdf(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Extract text and optionally OCR page images from a PDF file.

    Args:
        params: {"file_path": str, "page_start": int (0-based, optional),
                 "page_end": int (exclusive, optional), "extract_tables": bool (default False),
                 "enable_ocr": bool (default True) — render pages and OCR them}
        context: Execution context

    Returns:
        {"type": "document", "tool": "read_pdf", "content": str,
         "metadata": {file_name, file_size, page_count, total_chars, ocr_pages, ...}}
    """
    import pdfplumber

    file_path = params.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return {"type": "document", "tool": "read_pdf", "content": "", "metadata": {}, "error": f"File not found: {file_path}"}

    try:
        page_start = params.get("page_start", 0)
        page_end = params.get("page_end")  # None means all remaining pages
        enable_ocr = params.get("enable_ocr", True)

        pages_text: List[str] = []
        ocr_pages: List[int] = []
        table_data = []
        total_pages = 0

        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)
            end = page_end if page_end is not None else total_pages
            for i in range(page_start, min(end, total_pages)):
                page = pdf.pages[i]

                # Step 1: Extract selectable text
                text = page.extract_text() or ""
                page_parts = [f"--- Page {i + 1} ---"]
                if text.strip():
                    page_parts.append(text.strip())

                # Step 2: OCR the rendered page image to capture content in screenshots
                if enable_ocr:
                    ocr_content = _ocr_page(page, i + 1)
                    if ocr_content:
                        page_parts.append(ocr_content)
                        ocr_pages.append(i + 1)

                pages_text.append("\n".join(page_parts))

                if params.get("extract_tables"):
                    tables = page.extract_tables()
                    for t in tables:
                        table_data.append({"page": i + 1, "rows": t})

        content = "\n\n".join(pages_text).strip()

        metadata = {
            "file_name": os.path.basename(file_path),
            "file_size": os.path.getsize(file_path),
            "page_count": total_pages,
            "extracted_pages": min(end - page_start, total_pages),
            "ocr_pages": len(ocr_pages),
            "total_chars": len(content),
        }
        if table_data:
            metadata["tables"] = table_data

        return {
            "type": "document",
            "tool": "read_pdf",
            "content": content or "(empty PDF or no extractable text)",
            "metadata": metadata,
            "error": None,
        }
    except Exception as e:
        logger.error("read_pdf failed: %s", e)
        return {"type": "document", "tool": "read_pdf", "content": "", "metadata": {}, "error": str(e)}


def _ocr_page(page: Any, page_number: int) -> str:
    """Render a PDF page to an image and run OCR on it.

    Returns a formatted string with OCR content, or empty string on failure.
    """
    from app.tools.tesseract_helper import ocr_image

    # Temporary file for the rendered page image
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        # Render page at 200 DPI for good OCR quality
        pil_image = page.to_image(resolution=200)
        pil_image.save(tmp, format="PNG")
        tmp.close()

        text, error = ocr_image(tmp.name, lang="chi_sim+eng")
        if error or not text or text == "(no text detected)":
            return ""

        # Deduplicate: skip OCR lines that are very short or appear to be noise
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        meaningful = [l for l in lines if len(l) > 2]
        if not meaningful:
            return ""

        ocr_text = "\n".join(meaningful)
        return f"\n[Page {page_number} - OCR Content]\n{ocr_text}"
    except Exception as e:
        logger.debug("Page %d OCR failed: %s", page_number, e)
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


async def extract_pdf_metadata(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Extract metadata from a PDF file without reading full content.

    Args:
        params: {"file_path": str}
        context: Execution context

    Returns:
        {"type": "document", "tool": "extract_pdf_metadata",
         "content": str (summary), "metadata": {file_name, file_size, page_count, author, title, subject}}
    """
    import pdfplumber

    file_path = params.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return {"type": "document", "tool": "extract_pdf_metadata", "content": "", "metadata": {}, "error": f"File not found: {file_path}"}

    try:
        with pdfplumber.open(file_path) as pdf:
            meta = pdf.metadata or {}
            page_count = len(pdf.pages)
            info = {
                "file_name": os.path.basename(file_path),
                "file_size": os.path.getsize(file_path),
                "page_count": page_count,
                "author": meta.get("Author", ""),
                "title": meta.get("Title", ""),
                "subject": meta.get("Subject", ""),
                "producer": meta.get("Producer", ""),
                "created": str(meta.get("CreationDate", "")),
            }
            summary_parts = [
                f"File: {info['file_name']} ({info['file_size']} bytes)",
                f"Pages: {info['page_count']}",
            ]
            if info["title"]:
                summary_parts.append(f"Title: {info['title']}")
            if info["author"]:
                summary_parts.append(f"Author: {info['author']}")

            return {
                "type": "document",
                "tool": "extract_pdf_metadata",
                "content": "\n".join(summary_parts),
                "metadata": info,
                "error": None,
            }
    except Exception as e:
        logger.error("extract_pdf_metadata failed: %s", e)
        return {"type": "document", "tool": "extract_pdf_metadata", "content": "", "metadata": {}, "error": str(e)}
