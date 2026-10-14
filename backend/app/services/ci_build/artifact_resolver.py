"""Artifact 名称解析（移植自 BuildAutoFetch src/artifact_resolver.py）。

只保留 resolve_artifacts（下载清单解析）；固件重打包输入解析
（resolve_pack_inputs）随 packer 一并略去。
"""

import fnmatch
import logging
from typing import List, Optional

from .google_api import get_artifact_download_url, list_artifacts

logger = logging.getLogger(__name__)


def extract_prefix(target: str) -> str:
    """Remove known suffixes (-userdebug, -user, -eng) from target."""
    for suffix in ("-userdebug", "-user", "-eng"):
        if target.endswith(suffix):
            return target[: -len(suffix)]
    return target


def _find_match(artifacts: List[str], pattern: str) -> Optional[str]:
    """Return the first artifact path matching the glob pattern."""
    for artifact in artifacts:
        if fnmatch.fnmatch(artifact, pattern):
            return artifact
    return None


def resolve_artifacts(
    target_type: str,
    build_id: str,
    target: str,
    auth,
    project_id: str = "",
    release_candidate_name: str = "",
    prefix: str = "",
) -> List[str]:
    """
    Resolve the list of artifact paths to download for a given build.

    Signed artifacts are constructed from the build metadata because the build
    server does not always include them in the artifact list returned by
    list_artifacts, even though they are accessible via direct path.

    For userdebug builds the server's artifact list is matched first so that
    timestamp-suffixed names (e.g. ``*-fastboot-flashall-<id>_<ts>.zip``) are
    found.  If the list endpoint is unavailable or rate-limited, standard names
    are used as a fallback.

    Every candidate is then verified against the download-URL endpoint (which
    remains reachable even when list operations are rate-limited) and
    non-existent artifacts are dropped.

    Args:
        target_type: "user" or "userdebug"
        build_id: The build ID string
        target: Full target name, e.g. "CHIPX1-user" or "CHIPX1-userdebug"
        auth: Auth object providing request headers
        project_id: Optional Google Cloud project ID
        release_candidate_name: Release candidate name from the build metadata
            (e.g. "URT2.260618.011"), used to construct the signed OTA name.
        prefix: Target prefix, e.g. "CHIPX1". If empty, extracted from target.

    Returns:
        List of artifact paths to download.
    """
    if not prefix:
        prefix = extract_prefix(target)

    candidates: List[str] = []

    if target_type == "user":
        # Signed artifacts are not returned by list_artifacts but are
        # accessible via direct path under the signed/ subdirectory.
        candidates.append(f"signed/signed-{prefix}-img-{build_id}.zip")
        candidates.append(f"signed/signed-{prefix}-target_files-{build_id}.zip")
        candidates.append("otatools.zip")
        candidates.append("CItools.zip")
        if release_candidate_name:
            candidates.append(f"signed/signed-{prefix}-ota-{release_candidate_name}.zip")
        else:
            logger.warning(
                "No release_candidate_name available for %s, skipping signed OTA.",
                target,
            )

    elif target_type == "userdebug":
        patterns = [
            f"{prefix}-fastboot-flashall-{build_id}*",
            f"{prefix}-ota-{build_id}*",
            f"{prefix}-target_files-{build_id}*",
            "*otatools*.zip",
            "*CItools*.zip",
            "*citools*.zip",
            "CH_GTV5_950D5_aml_upgrade_package_all_secure.img",
        ]

        server_artifacts = list_artifacts(
            build_id=build_id,
            target=target,
            project_id=project_id,
            auth=auth,
        )

        if server_artifacts:
            matched = [
                m for m in (_find_match(server_artifacts, p) for p in patterns) if m
            ]
            # The server list may omit the pack inputs (target_files /
            # otatools / CItools) — probe the standard names too so they
            # enter the download set when they actually exist.
            extra = [
                f"{prefix}-target_files-{build_id}.zip",
                "otatools.zip",
                "CItools.zip",
            ]
            if matched:
                candidates = matched + [e for e in extra if e not in matched]
            else:
                logger.warning("No server artifact matched patterns; using standard names.")
                candidates = [p.replace("*", "") for p in patterns]
        else:
            # list_artifacts returned nothing (e.g. rate-limited or empty) —
            # fall back to standard, predictable artifact names.
            logger.warning(
                "list_artifacts unavailable/empty for %s; using standard artifact names.",
                target,
            )
            candidates = [
                f"{prefix}-fastboot-flashall-{build_id}.zip",
                f"{prefix}-ota-{build_id}.zip",
                f"{prefix}-target_files-{build_id}.zip",
                "otatools.zip",
                "CItools.zip",
                "CH_GTV5_950D5_aml_upgrade_package_all_secure.img",
            ]

    else:
        logger.warning("Unknown target_type '%s', returning empty artifact list.", target_type)
        return []

    # Verify each candidate exists via the download-URL endpoint. This stays
    # functional even when the list endpoints are rate-limited, and it drops
    # artifacts that do not exist for this build.
    resolved: List[str] = []
    for artifact in candidates:
        url = get_artifact_download_url(
            build_id=build_id,
            target=target,
            artifact=artifact,
            project_id=project_id,
            auth=auth,
        )
        if url:
            resolved.append(artifact)
        else:
            logger.warning("Artifact not found on server, skipping: %s", artifact)

    logger.info(
        "Resolved artifacts for type=%s build_id=%s -> %s",
        target_type, build_id, resolved,
    )
    return resolved
