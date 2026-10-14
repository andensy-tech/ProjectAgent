"""Artifact 下载（移植自 BuildAutoFetch src/fetcher.py，requests → httpx）。

同步阻塞 IO —— 调用方（pipeline）负责放入线程执行，避免阻塞事件循环。
"""

import logging
from pathlib import Path
from typing import Callable, Optional, Tuple

import httpx

from .google_api import get_artifact_download_url

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None]  # (bytes_written, total_or_-1)


class ArtifactFetcher:
    """Downloads artifacts from Google CI."""

    def __init__(self, output_dir: str, auth=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.auth = auth

    def download(
        self,
        build_id: str,
        target: str,
        artifact: str,
        project_id: str = "",
        progress_cb: Optional[ProgressCb] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Download the artifact for the given build.
        Returns (success, local_file_path).

        Files are stored under ``{output_dir}/{build_id}/{target}/`` so
        artifacts with identical names from different targets (e.g. user vs
        userdebug otatools.zip / target_files) never collide.
        """
        download_url = get_artifact_download_url(
            build_id=build_id,
            target=target,
            artifact=artifact,
            project_id=project_id,
            auth=self.auth,
        )

        if not download_url:
            logger.error("Failed to obtain download URL for artifact %s", artifact)
            return False, None

        # Store artifacts under a per-build-id, per-target subdirectory. If a
        # file with the same name already exists, remove it first so we always
        # keep the latest download for this build.
        safe_artifact = Path(artifact).name
        local_dir = self.output_dir / build_id / target
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / safe_artifact
        if local_path.exists():
            logger.info("Removing existing file %s before re-download.", local_path)
            local_path.unlink()

        logger.info("Downloading artifact to %s ...", local_path)
        try:
            timeout = httpx.Timeout(600.0, connect=30.0)
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", download_url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length") or -1)
                    written = 0
                    with open(local_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 512):
                            if not chunk:
                                continue
                            f.write(chunk)
                            written += len(chunk)
                            if progress_cb:
                                progress_cb(written, total)

            size = local_path.stat().st_size
            logger.info("Download complete: %s (%d bytes)", local_path, size)
            return True, str(local_path)

        except (httpx.HTTPError, OSError) as exc:
            logger.error("Download failed: %s", exc)
            # Clean up partial file
            if local_path.exists():
                local_path.unlink()
            return False, None
