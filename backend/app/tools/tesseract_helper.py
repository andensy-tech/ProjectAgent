"""
Tesseract helper — configures pytesseract with a bundled or system tesseract binary.

Looks for the tesseract binary in this order:
1. <project_root>/.tesseract/bin/tesseract (bundled portable install)
2. System PATH tesseract

Provides a single `ocr_image()` function used by pdf_tool, pptx_tool, and image_tool.
"""

import os
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_proj_root: str = ""
_tesseract_available: bool = False
_initialized: bool = False
_bundled_tesseract: bool = False
# Environment overrides for bundled tesseract (set temporarily per OCR call, not globally)
_env_overrides: dict = {}


def _find_project_root() -> str:
    """Walk up from this file to find the project root (where .tesseract/ lives)."""
    dir_ = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isdir(os.path.join(dir_, ".tesseract")) or os.path.isfile(os.path.join(dir_, ".env")):
            return dir_
        parent = os.path.dirname(dir_)
        if parent == dir_:
            break
        dir_ = parent
    return os.path.dirname(os.path.abspath(__file__))  # fallback


def init_tesseract() -> None:
    """Find and configure the tesseract binary once at module load time."""
    global _proj_root, _tesseract_available, _initialized
    if _initialized:
        return
    _initialized = True

    try:
        import pytesseract
    except ImportError:
        logger.warning("pytesseract not installed")
        return

    global _bundled_tesseract, _env_overrides

    _proj_root = _find_project_root()
    bundled = os.path.join(_proj_root, ".tesseract", "bin", "tesseract")

    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        tess_dir = os.path.join(_proj_root, ".tesseract")
        lib_dir = os.path.join(tess_dir, "lib")
        tessdata_dir = os.path.join(tess_dir, "tessdata")

        # Store env overrides instead of modifying global os.environ
        _env_overrides["LD_LIBRARY_PATH"] = f"{lib_dir}:{os.environ.get('LD_LIBRARY_PATH', '')}"
        _env_overrides["TESSDATA_PREFIX"] = tessdata_dir

        pytesseract.pytesseract.tesseract_cmd = bundled

        # Quick validation (set env temporarily for the probe call)
        old_env = {k: os.environ.get(k) for k in _env_overrides}
        for k, v in _env_overrides.items():
            os.environ[k] = v
        try:
            ver = pytesseract.get_tesseract_version()
            logger.info("Using bundled tesseract %s (TESSDATA=%s)", ver, tessdata_dir)
            _tesseract_available = True
            _bundled_tesseract = True
        except Exception as e:
            logger.warning("Bundled tesseract failed: %s", e)
        finally:
            for k, old_v in old_env.items():
                if old_v is not None:
                    os.environ[k] = old_v
                else:
                    os.environ.pop(k, None)

        if _tesseract_available:
            return

    # Fallback: system tesseract
    try:
        ver = pytesseract.get_tesseract_version()
        logger.info("Using system tesseract %s", ver)
        _tesseract_available = True
    except Exception:
        logger.info("No tesseract binary found (OCR unavailable)")


def ocr_image(file_path: str, lang: str = "chi_sim+eng") -> Tuple[str, str | None]:
    """Run OCR on an image file.

    Args:
        file_path: Path to the image file.
        lang: Tesseract language string (default "chi_sim+eng").

    Returns:
        (text, error) — one of them will be empty/None on success.
    """
    global _initialized
    if not _initialized:
        init_tesseract()

    if not _tesseract_available:
        return "", "tesseract-ocr not found. Install with: sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng"

    try:
        import pytesseract
        from PIL import Image

        # Temporarily set env vars for bundled tesseract (subprocess inherits os.environ)
        old_env = {}
        if _bundled_tesseract:
            for k, v in _env_overrides.items():
                old_env[k] = os.environ.get(k)
                os.environ[k] = v

        try:
            img = Image.open(file_path)
            text = pytesseract.image_to_string(img, lang=lang)
            img.close()
            return text.strip() or "(no text detected)", None
        finally:
            # Restore env vars to avoid affecting other modules
            if _bundled_tesseract:
                for k in _env_overrides:
                    if old_env.get(k) is not None:
                        os.environ[k] = old_env[k]
                    else:
                        os.environ.pop(k, None)
    except Exception as e:
        logger.error("ocr_image failed: %s", e)
        return "", str(e)


# Note: init_tesseract() is called lazily on first ocr_image() invocation.
# Module-level init was removed to avoid side effects on import
# (os.environ modification, subprocess probe).
