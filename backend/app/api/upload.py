"""
File Upload API — handles file uploads for document processing.
Supports doc/docx, pdf, xls/xlsx, ppt/pptx, and common image formats.
"""

import os
import uuid
import json
import logging
import mimetypes
from datetime import datetime, timedelta
from typing import Dict, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from app.api.deps import get_current_active_user
from app.core.config import settings
from app.core.security import verify_file_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])

# Allowed file extensions and their MIME types
ALLOWED_EXTENSIONS = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".eml": "message/rfc822",
    ".html": "text/html",
    ".htm": "text/html",
}


def _validate_extension(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXTENSIONS


def _cleanup_old_files():
    """Remove uploads older than 24 hours."""
    upload_dir = settings.UPLOAD_DIR
    if not os.path.exists(upload_dir):
        return
    cutoff = datetime.utcnow() - timedelta(hours=24)
    for file_id in os.listdir(upload_dir):
        meta_path = os.path.join(upload_dir, file_id, "metadata.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    meta = json.load(f)
                created = datetime.fromisoformat(meta.get("created_at", ""))
                if created < cutoff:
                    import shutil
                    shutil.rmtree(os.path.join(upload_dir, file_id), ignore_errors=True)
            except Exception:
                pass


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_active_user),
):
    """
    Upload a file for document processing.

    Returns file_id, file_name, file_size, content_type, and file_path.
    The file_id is used in subsequent chat messages to reference the file.
    """
    # Validate extension
    if not file.filename or not _validate_extension(file.filename):
        _, ext = os.path.splitext(file.filename or "")
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    # Validate size
    file_size = 0
    content = await file.read()
    file_size = len(content)
    if file_size > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large: {file_size} bytes (max {settings.MAX_UPLOAD_SIZE})")

    # Create storage directory
    file_id = str(uuid.uuid4())
    _, ext = os.path.splitext(file.filename)
    storage_dir = os.path.join(settings.UPLOAD_DIR, file_id)
    os.makedirs(storage_dir, exist_ok=True)

    # Save file
    safe_filename = f"original{ext}"
    file_path = os.path.join(storage_dir, safe_filename)
    with open(file_path, "wb") as f:
        f.write(content)

    # Save metadata
    mime_type, _ = mimetypes.guess_type(file.filename)
    metadata = {
        "file_id": file_id,
        "file_name": file.filename,
        "file_size": file_size,
        "content_type": mime_type or ALLOWED_EXTENSIONS.get(ext.lower(), "application/octet-stream"),
        "file_path": file_path,
        "created_at": datetime.utcnow().isoformat(),
    }
    with open(os.path.join(storage_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, ensure_ascii=False)

    logger.info(f"File uploaded: {file.filename} ({file_size} bytes) -> {file_id}")

    # Cleanup old files occasionally
    try:
        _cleanup_old_files()
    except Exception:
        pass

    return {
        "file_id": file_id,
        "file_name": file.filename,
        "file_size": file_size,
        "content_type": metadata["content_type"],
        "file_path": file_path,
    }


@router.get("/files/download")
async def download_generated_file(
    token: str,
    current_user: dict = Depends(get_current_active_user),
):
    """下载 Agent 生成的文件（Web Chat 文件回传）。

    文件可能是技能产物（技能目录）、内置工具产物（/tmp 或用户指定路径），
    路径不直接暴露：由后端签发 HMAC 令牌（见 core/security.create_file_token），
    令牌绑定用户且带有效期。

    NOTE: 本路由必须定义在 ``/files/{file_id}`` 之前，否则会被其吞掉。
    """
    user_id = current_user.get("user_id")
    file_path = verify_file_token(token, user_id)
    if not file_path:
        raise HTTPException(status_code=403, detail="下载链接无效或已过期，请在对话中重新生成文件")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="文件不存在或已被删除")

    file_name = os.path.basename(file_path)
    media_type, _ = mimetypes.guess_type(file_path)
    return FileResponse(
        file_path,
        filename=file_name,
        media_type=media_type or "application/octet-stream",
    )


@router.get("/files/{file_id}")
async def get_file_info(
    file_id: str,
    current_user: dict = Depends(get_current_active_user),
):
    """Get information about an uploaded file."""
    meta_path = os.path.join(settings.UPLOAD_DIR, file_id, "metadata.json")
    if not os.path.exists(meta_path):
        raise HTTPException(status_code=404, detail="File not found")
    with open(meta_path, "r") as f:
        metadata = json.load(f)
    return metadata


def get_uploaded_file_info(file_id: str) -> Dict:
    """Get uploaded file info by file_id (for internal use)."""
    meta_path = os.path.join(settings.UPLOAD_DIR, file_id, "metadata.json")
    if not os.path.exists(meta_path):
        return {}
    with open(meta_path, "r") as f:
        return json.load(f)
