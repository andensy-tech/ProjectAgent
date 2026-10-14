"""Google Android Build API v4 wrappers（移植自 BuildAutoFetch src/google_api.py）。

与原版差异：requests → httpx；auth 对象接口从 get_session() 改为
get_headers()；每次调用构建短生命周期 httpx.Client（与原版的
"不缓存 session" 语义一致，外部刷新的 token 立即生效）。
"""

import logging
from typing import Optional
from urllib.parse import quote

import httpx

from .auth import NoAuth

logger = logging.getLogger(__name__)

BASE_URL = "https://androidbuild-pa.googleapis.com/v4"
USER_PROJECT_HEADER = "X-Goog-User-Project"


def _get_headers(auth) -> dict:
    if auth is None:
        auth = NoAuth()
    return auth.get_headers()


def _api_get(url: str, auth, timeout: float = 60, retry_on_401: bool = True,
             project_id: str = "", follow_redirects: bool = True) -> httpx.Response:
    """Perform a GET request with automatic 401 retry.

    When the API returns HTTP 401, the auth object is asked to force-refresh
    its token and the request is retried once.

    ``project_id`` is sent as the ``X-Goog-User-Project`` quota-project header
    (Build API v4 convention).
    """
    headers = _get_headers(auth)
    if project_id:
        headers[USER_PROJECT_HEADER] = project_id

    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers=headers, follow_redirects=follow_redirects)

        if resp.status_code == 401 and retry_on_401 and hasattr(auth, "force_refresh"):
            logger.info("Received 401 — forcing token refresh and retrying...")
            if auth.force_refresh():
                headers = _get_headers(auth)
                if project_id:
                    headers[USER_PROJECT_HEADER] = project_id
                resp = client.get(url, headers=headers, follow_redirects=follow_redirects)
                logger.info("Retry after 401 completed (HTTP %s).", resp.status_code)

    return resp


def get_latest_good_build(
    branch: str = "",
    target: str = "",
    project_id: str = "",
    auth=None,
) -> Optional[dict]:
    """
    Query the latest successful build.
    At least one of branch or target must be provided.
    Returns a dict with build metadata or None if not found.

    Returned dict keys include:
      - build_id (str)
      - release_candidate_name (str, e.g. "URT2.260618.011")
      - signed (bool)
      - creation_timestamp_ms (str) — v4 returns ISO-8601 creationTimestamp
    """
    params = [
        "buildAttemptStatus=COMPLETE",
        "buildType=SUBMITTED",
        "pageSize=1",
        "successful=true",
    ]
    if branch:
        params.append(f"branches={quote(branch, safe='')}")
    if target:
        params.append(f"targets={quote(target, safe='')}")

    url = f"{BASE_URL}/builds?" + "&".join(params)

    logger.debug("GET %s", url)
    try:
        resp = _api_get(url, auth, timeout=60, project_id=project_id)
    except httpx.HTTPError as exc:
        logger.error("Failed to get latest good build: %s", exc)
        return None

    if resp.status_code != 200:
        logger.error(
            "Failed to get latest good build: HTTP %s - %s",
            resp.status_code,
            resp.text[:500],
        )
        return None

    data = resp.json()
    builds = data.get("builds", [])
    if not builds:
        logger.warning("No successful build found for branch=%s target=%s", branch, target)
        return None

    build = builds[0]
    return {
        "build_id": build.get("buildId"),
        "release_candidate_name": build.get("releaseCandidateName", ""),
        "signed": build.get("signed", False),
        "creation_timestamp_ms": str(build.get("creationTimestamp", "") or ""),
    }


def list_artifacts(
    build_id: str,
    target: str,
    project_id: str = "",
    auth=None,
) -> list:
    """
    List artifact paths for a given build attempt.
    Returns a list of artifact paths (e.g. 'signed/signed-CHIPX1-img-15732026.zip').
    """
    url = (
        f"{BASE_URL}/builds/{quote(build_id, safe='')}"
        f"/{quote(target, safe='')}"
        f"/attempts/latest/artifacts"
    )

    logger.debug("GET %s", url)
    try:
        resp = _api_get(url, auth, timeout=60, project_id=project_id)
    except httpx.HTTPError as exc:
        logger.error("Failed to list artifacts for %s/%s: %s", build_id, target, exc)
        return []

    if resp.status_code != 200:
        logger.error(
            "Failed to list artifacts: HTTP %s - %s",
            resp.status_code,
            resp.text[:500],
        )
        return []

    data = resp.json()
    artifacts = data.get("artifacts", [])
    paths = []
    for item in artifacts:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict):
            # API may return objects with 'name', 'path', or 'artifactId'
            for key in ("name", "path", "artifactId"):
                val = item.get(key)
                if val:
                    paths.append(str(val))
                    break

    logger.info("Listed %d artifacts for %s/%s", len(paths), build_id, target)
    return paths


def get_artifact_download_url(
    build_id: str,
    target: str,
    artifact: str,
    project_id: str = "",
    auth=None,
) -> Optional[str]:
    """
    Get the signed download URL for an artifact.
    Returns the direct download URL string or None on failure.
    """
    url = (
        f"{BASE_URL}/builds/{quote(build_id, safe='')}"
        f"/{quote(target, safe='')}"
        f"/attempts/latest/artifacts/{quote(artifact, safe='')}/url"
    )

    logger.debug("GET %s", url)
    try:
        # follow_redirects=False: the API returns a 302 redirect whose Location
        # header *is* the signed download URL we need.
        resp = _api_get(url, auth, timeout=60, project_id=project_id,
                        follow_redirects=False)
    except httpx.HTTPError as exc:
        logger.error("Failed to get artifact URL for %s: %s", artifact, exc)
        return None

    if resp.status_code in (301, 302, 307, 308):
        download_url = resp.headers.get("Location", "").strip()
        if download_url:
            return download_url

    if resp.status_code != 200:
        logger.error(
            "Failed to get artifact URL: HTTP %s - %s",
            resp.status_code,
            resp.text[:500],
        )
        return None

    # The response body may be JSON with signedUrl or plain text
    content_type = resp.headers.get("Content-Type", "")
    if "application/json" in content_type:
        data = resp.json()
        download_url = data.get("signedUrl", "").strip()
    else:
        download_url = resp.text.strip()

    if not download_url:
        logger.error("Empty download URL returned by API.")
        return None

    return download_url
