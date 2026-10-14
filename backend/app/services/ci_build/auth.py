"""OAuth2 认证与 token cache 管理（移植自 BuildAutoFetch src/auth.py）。

与原版差异：网络层 requests → httpx；get_session() 改为 get_headers()
（返回请求头 dict，由 google_api.py 构建短生命周期 httpx.Client）。
token cache 格式与 Go fetch_artifact 工具兼容，额外保存 refresh_token
支持自动续期。
"""

import json
import logging
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/androidbuild.internal"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


class TokenCache:
    """Read / write token cache compatible with Go fetch_artifact format."""

    def __init__(self, cache_path: str):
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Optional[dict]:
        if not self.cache_path.exists():
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load token cache: %s", exc)
            return None

    def save(self, token: dict) -> None:
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(token, f, indent=2, ensure_ascii=False)
        try:
            os_chmod = __import__("os").chmod
            os_chmod(self.cache_path, 0o600)
        except OSError:
            pass
        logger.info("Saved token cache to %s", self.cache_path)

    def is_expired(self, token: dict) -> bool:
        expiry_str = token.get("expiry")
        if not expiry_str:
            return True
        try:
            expiry = datetime.fromisoformat(expiry_str)
            return datetime.now(timezone.utc) >= expiry.astimezone(timezone.utc)
        except ValueError:
            return True

    def needs_refresh(self, token: dict, buffer_minutes: int = 5) -> bool:
        """Check if token is expired or close to expiry (within buffer)."""
        if self.is_expired(token):
            return True
        expiry_str = token.get("expiry")
        if not expiry_str:
            return True
        try:
            expiry = datetime.fromisoformat(expiry_str)
            threshold = expiry.astimezone(timezone.utc) - timedelta(minutes=buffer_minutes)
            return datetime.now(timezone.utc) >= threshold
        except ValueError:
            return True


class AuthRequiredError(Exception):
    """无可用 token 且需要交互式 OAuth 授权时抛出。"""


class GoogleAuth:
    """Handles OAuth2 for Android Build Internal API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_cache_path: str,
        port: int = 10502,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.port = port
        self.cache = TokenCache(token_cache_path)

    def get_headers(self) -> Dict[str, str]:
        """返回带合法 access_token 的请求头。

        token 过期或临近过期（5 分钟缓冲）时用 refresh_token 自动续期。
        每次调用都重新读 cache，外部刷新的 token 可立即生效。

        Raises:
            AuthRequiredError: 无可用 token 且无法自动续期，需先跑授权脚本。
        """
        token = self.cache.load()

        if token:
            if self.cache.needs_refresh(token):
                if token.get("refresh_token"):
                    logger.info("Access token expired/expiring, refreshing...")
                    refreshed = self._refresh_token(token["refresh_token"])
                    if refreshed:
                        self.cache.save(refreshed)
                        token = refreshed
                    else:
                        logger.warning("Token refresh failed, will retry on next call.")
                else:
                    logger.warning("Token expired but no refresh_token available.")

            if token and token.get("access_token"):
                return {
                    "Authorization": f"Bearer {token['access_token']}",
                    "Accept": "application/json",
                }

        raise AuthRequiredError(
            "无有效 OAuth token。请先运行一次性授权脚本完成 Google 账号授权：\n"
            "  cd backend && source venv/bin/activate && python scripts/ci_build_auth.py"
        )

    def has_valid_token(self) -> bool:
        """Check whether a usable token exists (cached or refreshable)."""
        token = self.cache.load()
        if token is None:
            return False
        if not self.cache.needs_refresh(token):
            return True
        if token.get("refresh_token"):
            refreshed = self._refresh_token(token["refresh_token"])
            if refreshed:
                self.cache.save(refreshed)
                return True
        return False

    def force_refresh(self) -> bool:
        """API 返回 401 时强制刷新（服务端提前使 token 失效的场景）。"""
        token = self.cache.load()
        if token and token.get("refresh_token"):
            refreshed = self._refresh_token(token["refresh_token"])
            if refreshed:
                self.cache.save(refreshed)
                logger.info("Token force-refreshed after 401.")
                return True
        return False

    def authorize(self) -> bool:
        """Run the interactive OAuth flow and save the token.

        Returns:
            True on success, False on failure.
        """
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            logger.error(
                "google-auth-oauthlib 未安装，无法执行交互式授权：pip install google-auth-oauthlib"
            )
            return False
        try:
            new_token = self._run_oauth_flow(InstalledAppFlow)
            self.cache.save(new_token)
            logger.info("Authorization successful, token cached.")
            return True
        except Exception as exc:
            logger.error("Authorization failed: %s", exc)
            return False

    def _refresh_token(self, refresh_token: str) -> Optional[dict]:
        """Use refresh_token to get a new access_token without user interaction."""
        try:
            resp = httpx.post(
                TOKEN_URI,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            new_refresh_token = data.get("refresh_token", refresh_token)

            return {
                "access_token": access_token,
                "refresh_token": new_refresh_token,
                "token_type": data.get("token_type", "Bearer"),
                "expiry": expiry.isoformat(),
            }
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("Refresh token request failed: %s", exc)
            return None

    def _run_oauth_flow(self, installed_app_flow_cls) -> dict:
        client_config = {
            "installed": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [f"http://localhost:{self.port}"],
            }
        }

        flow = installed_app_flow_cls.from_client_config(client_config, SCOPES)
        flow.redirect_uri = f"http://localhost:{self.port}"

        auth_url, state = flow.authorization_url(
            access_type="offline",
            prompt="select_account consent",
            include_granted_scopes="true",
        )

        print(f"\n请用浏览器访问以下 URL 完成授权:\n\n  {auth_url}\n", flush=True)
        try:
            webbrowser.open(auth_url, new=1, autoraise=True)
        except Exception:
            pass

        credentials = flow.run_local_server(
            port=self.port,
            open_browser=False,
            authorization_prompt_message=None,
            redirect_uri_trailing_slash=False,
            state=state,
        )

        return {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_type": "Bearer",
            "expiry": credentials.expiry.isoformat() if credentials.expiry else "",
        }


class NoAuth:
    """No authentication – for public artifacts."""

    def get_headers(self) -> Dict[str, str]:
        return {"Accept": "application/json"}
