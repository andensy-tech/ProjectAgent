"""Google CI (Android Build) 构建查询/下载/FTPS 上传服务包。

代码移植自 BuildAutoFetch 项目（/path/to/BuildAutoFetch），
针对 ProjectAgent 集成做了以下适配：

- 网络层 requests → httpx（与 backend 依赖一致）
- 所有路径参数化（token cache / 下载目录按 user_id 隔离在 backend/data/ci_build/）
- 配置不从 YAML 读取，由调用方（内置工具）从 UserSetting 注入
- 去掉 packer（固件重打包）/ notifier（邮件通知）/ 定时调度，仅保留
  查询、artifact 解析、下载、FTPS 上传四个能力
"""

from .config import (
    CiBuildConfig,
    CiBuildConfigError,
    AuthConfig,
    FtpConfig,
    DEFAULT_CI_BUILD_CONFIG,
    ci_build_data_dir,
    storage_dir,
    token_cache_path,
    config_from_dict,
)
from .auth import GoogleAuth, NoAuth, AuthRequiredError, TokenCache
from .google_api import get_latest_good_build, list_artifacts, get_artifact_download_url
from .artifact_resolver import resolve_artifacts
from .fetcher import ArtifactFetcher
from .uploader import FTUploader
from .pipeline import query_latest_build, run_fetch_pipeline, CiBuildError

__all__ = [
    "CiBuildConfig", "CiBuildConfigError", "AuthConfig", "FtpConfig",
    "DEFAULT_CI_BUILD_CONFIG", "ci_build_data_dir", "storage_dir",
    "token_cache_path", "config_from_dict",
    "GoogleAuth", "NoAuth", "AuthRequiredError", "TokenCache",
    "get_latest_good_build", "list_artifacts", "get_artifact_download_url",
    "resolve_artifacts", "ArtifactFetcher", "FTUploader",
    "query_latest_build", "run_fetch_pipeline", "CiBuildError",
]
