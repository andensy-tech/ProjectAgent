#!/usr/bin/env python3
"""CI 构建 Google OAuth 一次性授权脚本。

首次使用（或 refresh_token 失效时）在服务器上运行本脚本完成 Google
账号授权；授权成功后 token（含 refresh_token）写入
backend/data/ci_build/tokens/token_{user_id}.json，之后由服务自动续期，
无需重复授权。

用法：
    cd backend && source venv/bin/activate
    python scripts/ci_build_auth.py            # 默认 admin (user_id=1)
    python scripts/ci_build_auth.py --user 2   # 指定用户

前置条件：
    1. 设置页「CI 构建」已填写 OAuth client_id / client_secret（私有分支）
    2. 本机可访问 Google（授权回调监听 localhost:10502，需在服务器本机
       或用 SSH 端口转发 -L 10502:localhost:10502 访问授权 URL）
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

# 允许以脚本方式直接运行（python scripts/ci_build_auth.py）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> int:
    parser = argparse.ArgumentParser(description="CI 构建 Google OAuth 一次性授权")
    parser.add_argument("--user", type=int, default=1,
                        help="用户 ID（默认 1 = admin）")
    parser.add_argument("--client-id", default="",
                        help="直接传入 client_id（跳过读用户配置）")
    parser.add_argument("--client-secret", default="",
                        help="直接传入 client_secret（跳过读用户配置）")
    args = parser.parse_args()

    from app.services.ci_build import config_from_dict, token_cache_path
    from app.services.ci_build.auth import GoogleAuth
    from app.services.settings_service import get_ci_build_config_raw

    if args.client_id and args.client_secret:
        client_id, client_secret = args.client_id, args.client_secret
    else:
        raw = await get_ci_build_config_raw(args.user)
        cfg = config_from_dict(raw)
        client_id, client_secret = cfg.auth.client_id, cfg.auth.client_secret
        if not client_id or not client_secret:
            print(
                f"用户 {args.user} 未配置 OAuth client_id/client_secret。\n"
                f"请先在设置页「CI 构建」Tab 填写，或用 --client-id/--client-secret 传入。",
                file=sys.stderr,
            )
            return 1

    cache_path = token_cache_path(args.user)
    auth = GoogleAuth(
        client_id=client_id,
        client_secret=client_secret,
        token_cache_path=str(cache_path),
    )

    print(f"当前用户: user_id={args.user} ({getpass.getuser()})")
    print(f"token cache: {cache_path}\n")

    if not auth.authorize():
        print("授权失败，请重试。", file=sys.stderr)
        return 1

    print(f"\n授权成功！token 已保存到 {cache_path}")
    print("之后 Agent 聊天中可直接查询/下载构建，token 会自动续期。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
