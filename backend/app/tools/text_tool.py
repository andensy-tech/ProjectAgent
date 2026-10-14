"""
Text file (.txt,.log,.md,.csv,.json,.xml,.yaml,.ini,.cfg) tool — read text files.
Uses Python standard library only. Supports auto-detection of common encodings.
"""

import os
import codecs
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Common text file encodings to try in order
_ENCODINGS = ["utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030", "latin-1", "shift-jis", "euc-kr", "big5", "utf-16"]


def _detect_encoding(file_path: str, hint: str = "") -> str:
    """Detect text file encoding by trying common encodings.

    First tries the user-provided hint, then auto-detects by BOM,
    then tries common encodings in order.

    Returns the first encoding that decodes without error.
    Falls back to 'utf-8' with replacement as last resort.
    """
    # Try user hint first
    if hint and hint.lower() not in ("auto", ""):
        try:
            with open(file_path, "r", encoding=hint) as f:
                f.read(1024)
            return hint
        except (UnicodeDecodeError, LookupError):
            pass

    # Check for BOM
    with open(file_path, "rb") as f:
        raw = f.read(4)

    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    if raw.startswith(codecs.BOM_UTF32_LE) or raw.startswith(codecs.BOM_UTF32_BE):
        return "utf-32"

    # Try common encodings
    for enc in _ENCODINGS:
        try:
            with open(file_path, "r", encoding=enc) as f:
                f.read(1024)
            return enc
        except UnicodeDecodeError:
            continue

    return "utf-8"  # last resort


async def read_text_file(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Read a text file and return its content.

    Supports plain text files with encoding auto-detection.
    Suitable for .txt, .log, .md, .csv, .json, .xml, .yaml, .ini, .cfg files.

    Args:
        params: {
            "file_path": str (required) — absolute path to the text file,
            "encoding": str (optional) — force a specific encoding (default: auto-detect)
        }
        context: Execution context

    Returns:
        {"type": "document", "tool": "read_text_file", "content": str,
         "metadata": {file_name, file_size, encoding, line_count},
         "error": str | None}
    """
    file_path = params.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return {
            "type": "document", "tool": "read_text_file", "content": "",
            "metadata": {}, "error": f"File not found: {file_path}",
        }

    if os.path.isdir(file_path):
        return {
            "type": "document", "tool": "read_text_file", "content": "",
            "metadata": {}, "error": f"Path is a directory, not a file: {file_path}",
        }

    try:
        encoding_hint = params.get("encoding", "auto")
        encoding = _detect_encoding(file_path, encoding_hint)

        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()

        return {
            "type": "document",
            "tool": "read_text_file",
            "content": content,
            "metadata": {
                "file_name": os.path.basename(file_path),
                "file_size": os.path.getsize(file_path),
                "encoding": encoding,
                "line_count": content.count("\n") + 1,
            },
            "error": None,
        }
    except (IOError, OSError) as e:
        logger.error("read_text_file I/O error: %s", e)
        return {
            "type": "document", "tool": "read_text_file",
            "content": "", "metadata": {}, "error": f"读取文件失败: {str(e)}",
        }
    except Exception as e:
        logger.error("read_text_file failed: %s", e)
        return {
            "type": "document", "tool": "read_text_file",
            "content": "", "metadata": {}, "error": str(e),
        }
