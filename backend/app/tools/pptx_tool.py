"""
PowerPoint (.pptx) tool — read and create presentations using python-pptx.

Supports text extraction and OCR of embedded images in slides.
"""

import os
import tempfile
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


async def read_pptx(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Extract text and optionally OCR embedded images from a PowerPoint file.

    Args:
        params: {"file_path": str, "extract_notes": bool (default False),
                 "enable_ocr": bool (default True) — OCR embedded images in slides}
        context: Execution context

    Returns:
        {"type": "document", "tool": "read_pptx", "content": str,
         "metadata": {file_name, file_size, slide_count,
                      slides: [{slide_number, texts, notes}]}}
    """
    from pptx import Presentation

    file_path = params.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return {"type": "document", "tool": "read_pptx", "content": "", "metadata": {}, "error": f"File not found: {file_path}"}

    try:
        prs = Presentation(file_path)
        slides_data = []
        all_texts = []
        enable_ocr = params.get("enable_ocr", True)
        ocr_slides = 0

        for i, slide in enumerate(prs.slides, 1):
            slide_texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = para.text.strip()
                        if text:
                            slide_texts.append(text)
                if shape.has_table:
                    table = shape.table
                    for row in table.rows:
                        row_texts = [cell.text.strip() for cell in row.cells]
                        slide_texts.append(" | ".join(row_texts))

            slide_info: Dict[str, Any] = {
                "slide_number": i,
                "texts": slide_texts,
            }
            all_texts.append(f"--- Slide {i} ---")
            all_texts.extend(slide_texts)

            # OCR embedded images in the slide
            if enable_ocr:
                ocr_results = _ocr_slide_images(slide, i)
                if ocr_results:
                    slide_info["ocr_images"] = len(ocr_results)
                    ocr_slides += 1
                    all_texts.append("")
                    for idx, ocr_text in enumerate(ocr_results):
                        all_texts.append(f"[Slide {i} - Image {idx + 1} - OCR]")
                        all_texts.append(ocr_text)

            if params.get("extract_notes"):
                notes = slide.notes_slide.notes_text_frame.text.strip() if slide.has_notes_slide else ""
                slide_info["notes"] = notes
                if notes:
                    all_texts.append(f"[Notes]: {notes}")

            slides_data.append(slide_info)

        content = "\n".join(all_texts)

        return {
            "type": "document",
            "tool": "read_pptx",
            "content": content.strip() or "(empty presentation)",
            "metadata": {
                "file_name": os.path.basename(file_path),
                "file_size": os.path.getsize(file_path),
                "slide_count": len(slides_data),
                "ocr_slides": ocr_slides,
                "slides": slides_data,
            },
            "error": None,
        }
    except Exception as e:
        logger.error("read_pptx failed: %s", e)
        return {"type": "document", "tool": "read_pptx", "content": "", "metadata": {}, "error": str(e)}


def _ocr_slide_images(slide: Any, slide_number: int) -> List[str]:
    """Extract embedded images from a slide and OCR them.

    Returns a list of OCR text strings (one per image found).
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from app.tools.tesseract_helper import ocr_image

    results: List[str] = []
    for shape in slide.shapes:
        if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
            continue
        if not hasattr(shape, "image") or shape.image is None:
            continue

        # Determine file extension from content type
        content_type = getattr(shape.image, "content_type", "") or ""
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/tiff": ".tiff",
        }
        suffix = ext_map.get(content_type, ".png")

        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(shape.image.blob)
            tmp.close()

            text, error = ocr_image(tmp.name, lang="chi_sim+eng")
            if error or not text or text == "(no text detected)":
                continue
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            meaningful = [l for l in lines if len(l) > 2]
            if meaningful:
                results.append("\n".join(meaningful))
        except Exception as e:
            logger.debug("Slide %d image OCR failed: %s", slide_number, e)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    return results


async def write_pptx(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Create a PowerPoint file from structured data.

    Args:
        params: {"file_path": str, "title": str, "slides": [{"title": str, "content": [str], "notes": str}]}
        context: Execution context

    Returns:
        {"type": "document", "tool": "write_pptx", "content": "file_path",
         "metadata": {file_name, file_size, slide_count}}
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    file_path = params.get("file_path", "")
    if not file_path:
        return {"type": "document", "tool": "write_pptx", "content": "", "metadata": {}, "error": "file_path is required"}

    try:
        prs = Presentation()

        # Title slide
        title_text = params.get("title", "Presentation")
        title_slide = prs.slides.add_slide(prs.slide_layouts[0])
        title_slide.shapes.title.text = title_text

        # Content slides
        slide_count = 1
        for slide_def in params.get("slides", []):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide_title = slide_def.get("title", "")
            slide_content = slide_def.get("content", [])

            if slide.shapes.title:
                slide.shapes.title.text = slide_title

            body_shape = slide.placeholders[1] if len(slide.placeholders) > 1 else None
            if body_shape and slide_content:
                text_frame = body_shape.text_frame
                text_frame.clear()
                for i, item in enumerate(slide_content):
                    if i == 0:
                        text_frame.paragraphs[0].text = item
                    else:
                        p = text_frame.add_paragraph()
                        p.text = item

            # Notes
            notes_text = slide_def.get("notes", "")
            if notes_text:
                notes_slide = slide.notes_slide
                notes_slide.notes_text_frame.text = notes_text

            slide_count += 1

        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        prs.save(file_path)

        return {
            "type": "document",
            "tool": "write_pptx",
            "content": file_path,
            "metadata": {
                "file_name": os.path.basename(file_path),
                "file_size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                "slide_count": slide_count,
            },
            "error": None,
        }
    except Exception as e:
        logger.error("write_pptx failed: %s", e)
        return {"type": "document", "tool": "write_pptx", "content": "", "metadata": {}, "error": str(e)}
