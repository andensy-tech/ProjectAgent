"""FTPS 上传（移植自 BuildAutoFetch src/uploader.py）。

基于标准库 ftplib.FTP_TLS，同步阻塞 IO —— 调用方（pipeline）负责放入
线程执行。remote_dir 由调用方按 ``{根目录}/{YYYY-MM-DD}-{build_id}/{type}``
构造，与 BuildAutoFetch 的归档结构保持一致。
"""

import logging
from ftplib import FTP_TLS, error_perm
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None]  # (bytes_sent, total)


class _UploadProgress:
    """Tracks upload progress and logs every 100MB."""

    def __init__(self, total_size: int, cb: Optional[ProgressCb] = None,
                 log_interval_mb: int = 100):
        self.total_size = total_size
        self.cb = cb
        self.log_interval = log_interval_mb * 1024 * 1024
        self.transferred = 0
        self.last_logged = 0

    def update(self, chunk: bytes) -> None:
        """Callback for ftplib.storbinary; called after each block is sent."""
        self.transferred += len(chunk)
        if self.cb:
            self.cb(self.transferred, self.total_size)
        if self.transferred - self.last_logged >= self.log_interval:
            self.last_logged = self.transferred
            pct = min((self.transferred / self.total_size) * 100, 100.0) if self.total_size else 0
            logger.info(
                "Upload progress: %.1f%% (%d / %d MB)",
                pct,
                self.transferred // (1024 * 1024),
                self.total_size // (1024 * 1024),
            )


class FTUploader:
    """Uploads files to an FT server via FTPS (FTP-TLS)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str = "",
        remote_dir: str = "/",
    ):
        self.host = host
        self.port = port
        self.username = username.strip()
        self.password = password
        self.remote_dir = remote_dir

    def upload(self, local_path: str, max_retries: int = 3,
               progress_cb: Optional[ProgressCb] = None) -> bool:
        """Upload a single file with retry and progress logging.

        Args:
            local_path: Path to the local file to upload.
            max_retries: Maximum number of retry attempts on failure.
            progress_cb: Optional (bytes_sent, total) callback.

        Returns:
            True on success, False on failure.
        """
        local_file = Path(local_path)
        if not local_file.exists():
            logger.error("Local file does not exist: %s", local_path)
            return False

        remote_filename = local_file.name
        remote_path = f"{self.remote_dir.rstrip('/')}/{remote_filename}"
        file_size = local_file.stat().st_size

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                logger.info("Retrying upload (%d/%d)...", attempt, max_retries)

            ftps: Optional[FTP_TLS] = None
            try:
                ftps = FTP_TLS()
                ftps.encoding = "latin-1"

                logger.info("Connecting to FTPS %s:%s ...", self.host, self.port)
                ftps.connect(self.host, self.port, timeout=30)

                ftps.auth()
                ftps.login(self.username, self.password)
                ftps.prot_p()

                # Ensure remote directory exists
                self._mkdir_p(ftps, self.remote_dir)
                ftps.cwd(self.remote_dir)

                logger.info(
                    "Uploading %s (%d MB) -> %s:%s",
                    local_path, file_size // (1024 * 1024), self.host, remote_path
                )

                progress = _UploadProgress(file_size, cb=progress_cb)
                with open(local_path, "rb") as f:
                    ftps.storbinary(
                        f"STOR {remote_filename}",
                        f,
                        callback=progress.update,
                    )

                logger.info("Upload successful: %s", remote_path)
                return True

            except error_perm as exc:
                logger.error("FTPS permission error: %s", exc)
                # Permission errors are unlikely to succeed on retry
                return False
            except Exception as exc:
                logger.error("Upload failed (attempt %d/%d): %s", attempt, max_retries, exc)
                if attempt == max_retries:
                    logger.error("All upload attempts exhausted.")
                    return False
            finally:
                if ftps is not None:
                    try:
                        ftps.quit()
                    except Exception:
                        pass

        return False

    @staticmethod
    def _mkdir_p(ftps: FTP_TLS, remote_directory: str) -> None:
        """Recursively create remote directories."""
        if remote_directory == "/":
            return

        parts = remote_directory.strip("/").split("/")
        current = "/"
        for part in parts:
            current = f"{current.rstrip('/')}/{part}"
            try:
                ftps.cwd(current)
            except error_perm:
                ftps.mkd(current)
                ftps.cwd(current)
