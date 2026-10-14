"""
HTML (.html) tool — read and write HTML files using Python standard library.

read_html: extract visible text content + metadata using html.parser.
write_html: write HTML string to file. No third-party dependencies.
"""

import os
import logging
from html.parser import HTMLParser
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class _TextExtractor(HTMLParser):
    """HTMLParser subclass that extracts visible text content."""

    def __init__(self):
        super().__init__()
        self._text_parts: List[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self._text_parts.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._text_parts)


async def read_html(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Read an HTML file and extract visible text content + metadata.

    Args:
        params: {"file_path": str, "extract_text": bool (default True)}
        context: Execution context

    Returns:
        {"type": "document", "tool": "read_html", "content": str,
         "metadata": {file_name, file_size, title, raw_length, ...}}
    """
    file_path = params.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return {
            "type": "document", "tool": "read_html", "content": "",
            "metadata": {}, "error": f"File not found: {file_path}",
        }

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()

        metadata = {
            "file_name": os.path.basename(file_path),
            "file_size": os.path.getsize(file_path),
            "raw_length": len(raw),
        }

        # Extract <title>
        import re
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
        if title_match:
            metadata["title"] = title_match.group(1).strip()

        # Extract visible text
        if params.get("extract_text", True):
            extractor = _TextExtractor()
            extractor.feed(raw)
            content = extractor.get_text().strip() or "(empty or no visible text)"
        else:
            content = raw

        return {
            "type": "document",
            "tool": "read_html",
            "content": content,
            "metadata": metadata,
            "error": None,
        }
    except Exception as e:
        logger.error("read_html failed: %s", e)
        return {
            "type": "document", "tool": "read_html",
            "content": "", "metadata": {}, "error": str(e),
        }


async def write_html(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Write HTML content to a file.

    Args:
        params: {
            "file_path": str,
            "content": str (HTML string),
        }
        context: Execution context

    Returns:
        {"type": "document", "tool": "write_html", "content": "file_path",
         "metadata": {file_name, file_size, ...}}
    """
    file_path = params.get("file_path", "")
    if not file_path:
        return {
            "type": "document", "tool": "write_html",
            "content": "", "metadata": {}, "error": "file_path is required",
        }

    content = params.get("content", "")
    if not content:
        return {
            "type": "document", "tool": "write_html",
            "content": "", "metadata": {}, "error": "content is required",
        }

    try:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return {
            "type": "document",
            "tool": "write_html",
            "content": file_path,
            "metadata": {
                "file_name": os.path.basename(file_path),
                "file_size": os.path.getsize(file_path),
            },
            "error": None,
        }
    except Exception as e:
        logger.error("write_html failed: %s", e)
        return {
            "type": "document", "tool": "write_html",
            "content": "", "metadata": {}, "error": str(e),
        }
