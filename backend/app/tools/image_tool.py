"""
Image tool — read image metadata and perform OCR using Pillow + pytesseract.
"""

import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def read_image_metadata(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Extract metadata from an image file.

    Args:
        params: {"file_path": str}
        context: Execution context

    Returns:
        {"type": "document", "tool": "read_image_metadata",
         "content": str (summary), "metadata": {file_name, file_size, format, size, mode, exif}}
    """
    from PIL import Image

    file_path = params.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return {"type": "document", "tool": "read_image_metadata", "content": "", "metadata": {}, "error": f"File not found: {file_path}"}

    try:
        img = Image.open(file_path)
        info = {
            "file_name": os.path.basename(file_path),
            "file_size": os.path.getsize(file_path),
            "format": img.format or "unknown",
            "size": f"{img.width}x{img.height}",
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
        }

        # EXIF data
        exif_data = img._getexif()
        if exif_data:
            exif_info = {}
            for tag_id, value in exif_data.items():
                tag_name = Image.ExifTags.TAGS.get(tag_id, str(tag_id))
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8", errors="replace")
                    except Exception:
                        value = str(value)
                exif_info[tag_name] = str(value)[:200]
            info["exif"] = exif_info

        img.close()

        summary = (
            f"File: {info['file_name']} ({info['file_size']} bytes)\n"
            f"Format: {info['format']} | Size: {info['size']} | Mode: {info['mode']}"
        )

        return {
            "type": "document",
            "tool": "read_image_metadata",
            "content": summary,
            "metadata": info,
            "error": None,
        }
    except Exception as e:
        logger.error("read_image_metadata failed: %s", e)
        return {"type": "document", "tool": "read_image_metadata", "content": "", "metadata": {}, "error": str(e)}


async def ocr_image(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Extract text from an image using OCR.

    Uses bundled or system tesseract via tesseract_helper.

    Args:
        params: {"file_path": str, "language": str (default "chi_sim+eng")}
        context: Execution context

    Returns:
        {"type": "document", "tool": "ocr_image",
         "content": str (extracted text), "metadata": {file_name, language}}
    """
    from app.tools.tesseract_helper import ocr_image as _ocr

    file_path = params.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return {"type": "document", "tool": "ocr_image", "content": "", "metadata": {}, "error": f"File not found: {file_path}"}

    language = params.get("language", "chi_sim+eng")
    text, error = _ocr(file_path, lang=language)

    if error:
        return {"type": "document", "tool": "ocr_image", "content": "", "metadata": {}, "error": error}

    return {
        "type": "document",
        "tool": "ocr_image",
        "content": text or "(no text detected in image)",
        "metadata": {
            "file_name": os.path.basename(file_path),
            "file_size": os.path.getsize(file_path),
            "language": language,
        },
        "error": None,
    }
