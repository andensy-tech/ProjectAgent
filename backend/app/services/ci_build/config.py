"""CI 构建配置模型与数据目录约定。

配置数据本身存 UserSetting（key="ci_build"），本模块负责：
  - 定义默认配置与各字段结构
  - dict → CiBuildConfig 数据类解析 + 必填校验
  - 按用户隔离的数据目录（token cache / 下载存储）路径
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


class CiBuildConfigError(Exception):
    """CI 构建配置不完整或非法时抛出（消息面向用户，用中文）。"""


# ── 默认配置（与设置页表单字段一一对应）────────────────────────────────────

DEFAULT_CI_BUILD_CONFIG: Dict[str, Any] = {
    "branch": "",
    "target": "",
    "ota_flavor": "",
    "default_build_type": "userdebug",
    "auth": {
        "enabled": True,
        "client_id": "",
        "client_secret": "",
        "project_id": "",
    },
    "ftp": {
        "host": "",
        "port": 21,
        "username": "",
        "password": "",
        "remote_dir": "/",
    },
}


# ── 数据目录（按用户隔离）──────────────────────────────────────────────────

def ci_build_data_dir() -> Path:
    """backend/data/ci_build 根目录（与 project_agent.db 同级）。"""
    base = Path(__file__).resolve().parents[3] / "data" / "ci_build"
    base.mkdir(parents=True, exist_ok=True)
    return base


def token_cache_path(user_id: int) -> Path:
    """某用户的 OAuth token cache 文件路径（含 refresh_token，注意文件权限）。"""
    d = ci_build_data_dir() / "tokens"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"token_{user_id}.json"


def storage_dir(user_id: int) -> Path:
    """某用户的构建产物本地下载根目录。"""
    d = ci_build_data_dir() / "storage" / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 配置数据类 ──────────────────────────────────────────────────────────────

@dataclass
class AuthConfig:
    """Google OAuth2 配置（私有分支必需；公共分支可 disabled）。"""

    enabled: bool = True
    client_id: str = ""
    client_secret: str = ""
    project_id: str = ""


@dataclass
class FtpConfig:
    """FTPS 上传目标配置。"""

    host: str = ""
    port: int = 21
    username: str = ""
    password: str = ""
    remote_dir: str = "/"


@dataclass
class CiBuildConfig:
    """CI 构建完整配置。"""

    branch: str = ""
    target: str = ""
    ota_flavor: str = ""
    default_build_type: str = "userdebug"
    auth: AuthConfig = field(default_factory=AuthConfig)
    ftp: FtpConfig = field(default_factory=FtpConfig)

    # ── 校验（返回中文错误信息；None 表示通过）──

    def validate_query(self) -> Optional[str]:
        """查询最新构建所需的最小配置。"""
        if not self.branch and not self.target:
            return "未配置监控分支或目标：请先在设置页「CI 构建」中填写 branch 或 target。"
        return None

    def validate_download(self) -> Optional[str]:
        """下载 + FTPS 上传所需的完整配置。"""
        err = self.validate_query()
        if err:
            return err
        if not self.target:
            return "下载构建需要配置 target（目标基础名，如 CHIPX1）：请先在设置页「CI 构建」中填写。"
        if self.auth.enabled:
            if not self.auth.client_id or not self.auth.client_secret:
                return "私有分支下载需要 OAuth 凭据：请先在设置页「CI 构建」中填写 client_id / client_secret。"
        if not self.ftp.host or not self.ftp.username:
            return "FTPS 上传未配置完整：请先在设置页「CI 构建」中填写 FTP host / username。"
        return None

    # ── 构建认证对象 ──

    def build_auth(self, user_id: int):
        """按配置构造 GoogleAuth（或公共分支的 NoAuth）。"""
        from .auth import GoogleAuth, NoAuth

        if not self.auth.enabled:
            return NoAuth()
        return GoogleAuth(
            client_id=self.auth.client_id,
            client_secret=self.auth.client_secret,
            token_cache_path=str(token_cache_path(user_id)),
        )


def config_from_dict(data: Dict[str, Any]) -> CiBuildConfig:
    """UserSetting 中的 dict → CiBuildConfig（缺字段回退默认值）。"""
    data = data or {}
    auth = data.get("auth") or {}
    ftp = data.get("ftp") or {}
    try:
        port = int(ftp.get("port") or 21)
    except (TypeError, ValueError):
        port = 21
    return CiBuildConfig(
        branch=str(data.get("branch") or ""),
        target=str(data.get("target") or ""),
        ota_flavor=str(data.get("ota_flavor") or ""),
        default_build_type=str(data.get("default_build_type") or "userdebug"),
        auth=AuthConfig(
            enabled=bool(auth.get("enabled", True)),
            client_id=str(auth.get("client_id") or ""),
            client_secret=str(auth.get("client_secret") or ""),
            project_id=str(auth.get("project_id") or ""),
        ),
        ftp=FtpConfig(
            host=str(ftp.get("host") or ""),
            port=port,
            username=str(ftp.get("username") or ""),
            password=str(ftp.get("password") or ""),
            remote_dir=str(ftp.get("remote_dir") or "/"),
        ),
    )
