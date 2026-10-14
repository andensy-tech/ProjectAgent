"""
Confluence REST API Tool — 直接通过 Confluence REST API 获取页面完整内容（含正文）。

背景：Confluence MCP 服务端的 get_page_mcp_confluence 工具调用 REST API 时
未传递 expand=body.storage 参数，导致返回的 body 为空。此工具使用与 MCP 相同
的用户凭据，直接调用 Confluence REST API 并指定 expand 参数获取正文。

端点：
  - GET /rest/api/content/{pageId}?expand=body.storage,body.view,version,space
  - GET /rest/api/content/{pageId}/attachments  (图片下载+OCR)
"""

import html
import json
import logging
import os
import re
import tempfile
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _resolve_confluence_auth(
    params: Dict[str, Any],
    mcp_config: Dict[str, Any],
) -> tuple:
    """从用户 MCP 配置中提取 Confluence REST API 认证信息。

    Args:
        params: 工具参数（可包含 confluence_base_url 覆盖）
        mcp_config: 用户的 MCP 全量配置（来自 get_mcp_auth_dict）

    Returns:
        (rest_base_url: str, username: str, password: str)
        任一字段为空时表示配置缺失。
    """
    # 1. 优先使用参数中显式传入的 Confluence base URL
    #    兼容用户直接粘贴完整页面 URL 的场景（如 .../pages/viewpage.action?pageId=123）
    rest_base = (params.get("confluence_base_url") or "").strip().rstrip("/")
    if rest_base:
        # 去除 /display/... /rest/... /wiki/... /pages/... 等路径后缀
        for suffix in ("/display", "/rest", "/wiki", "/pages", "/viewpage.action", "/spaces"):
            idx = rest_base.find(suffix)
            if idx > 0:
                rest_base = rest_base[:idx]
                break
        # 去掉查询参数（?pageId=... 等）
        if "?" in rest_base:
            rest_base = rest_base.split("?")[0]
        rest_base = rest_base.rstrip("/")

    # 2. 从 MCP 配置中提取 confluence 服务器信息
    cfg = mcp_config.get("confluence") or {}
    mcp_url = cfg.get("url", "").rstrip("/")

    auth = cfg.get("auth") or {}
    username = auth.get("username") or auth.get("user") or ""
    password = auth.get("password") or auth.get("pass") or ""

    # 3. 尝试从 auth 中提取 server_url（类似 Gerrit 的机制）
    server_url = auth.get("server_url", "").rstrip("/")
    if server_url and not rest_base:
        rest_base = server_url

    # 4. 从 MCP URL 推导（将 /mcp 后缀去掉作为 REST API 基地址的猜测）
    if not rest_base and mcp_url:
        if mcp_url.endswith("/mcp"):
            rest_base = mcp_url[:-4].rstrip("/")
        else:
            rest_base = mcp_url

    return rest_base, username, password


async def _get_confluence_http_client(
    params: Dict[str, Any],
    context: Dict[str, Any],
) -> Optional[dict]:
    """获取 Confluence REST API 的 HTTP 客户端 + 认证信息。

    返回 {"rest_base": str, "headers": dict}，或带 "error" 的 dict 表示失败。
    """
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"error": "缺少用户身份信息，无法获取 MCP 配置"}

    try:
        from app.services.settings_service import get_mcp_auth_dict
        mcp_config = await get_mcp_auth_dict(user_id)
    except Exception as e:
        logger.error("Failed to load MCP config for user %s: %s", user_id, e)
        return {"error": f"加载 MCP 配置失败: {e}"}

    rest_base, username, password = _resolve_confluence_auth(params, mcp_config)
    if not rest_base:
        return {"error": "Confluence 未配置 URL，请在 MCP 设置中配置 Confluence 服务器"}
    if not username or not password:
        return {"error": "Confluence 认证信息不完整，请在 MCP 设置中检查配置"}

    import base64
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    }
    return {"rest_base": rest_base, "headers": headers, "username": username, "password": password}


def _html_table_to_markdown(html_text: str) -> str:
    """将 HTML 中的 <table> 转换为 markdown 表格，保留行列结构。

    背景：Confluence storage 格式的表格单元格之间没有换行符，直接剥掉
    HTML 标签会让整张表粘连成一段连续文本，LLM 无法定位具体列（如
    "项目分支链接"）也无法逐行提取数据。此函数在剥标签前先把每个
    <table> 转成 markdown 表格，使 LLM 拿到的是结构化行列表格。

    Args:
        html_text: 含 <table> 的 HTML 片段（单元格内可能已含 [图片] 占位符）

    Returns:
        表格被替换为 markdown 表格的 HTML（表格外的其余部分原样保留）
    """
    if "<table" not in html_text.lower():
        return html_text

    def _cell_text(cell_html: str) -> str:
        """单元格 HTML -> 单行纯文本：<br> 转分隔、剥标签、解实体、压缩空白、转义管道符。"""
        text = re.sub(r"<br\s*/?>", "\n", cell_html, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)  # &nbsp; / &amp; / &lt; 等实体还原
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        text = text.replace("|", "\\|")
        # 单元格内换行用 · 连接，避免破坏 markdown 表格的一行结构
        text = text.replace("\n", " · ")
        return text

    def _replace_table(match: "re.Match") -> str:
        table_html = match.group(0)
        row_matches = re.findall(
            r"<tr[^>]*>(.*?)</tr>", table_html, re.IGNORECASE | re.DOTALL
        )
        parsed_rows: list = []
        max_cols = 0
        for row in row_matches:
            cells = re.findall(
                r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.IGNORECASE | re.DOTALL
            )
            if not cells:
                continue
            parsed_rows.append([_cell_text(c) for c in cells])
            max_cols = max(max_cols, len(cells))
        if not parsed_rows:
            return table_html

        # 首行作为表头
        header = (parsed_rows[0] + [""] * max_cols)[:max_cols]
        md = [
            "| " + " | ".join(header) + " |",
            "|" + "|".join(["---"] * max_cols) + "|",
        ]
        for row in parsed_rows[1:]:
            row = (row + [""] * max_cols)[:max_cols]
            md.append("| " + " | ".join(row) + " |")
        return "\n".join(md)

    return re.sub(
        r"<table[^>]*>.*?</table>",
        _replace_table,
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _extract_images_from_html(
    body_content: str,
    rest_base: str,
) -> tuple:
    """从 Confluence HTML 正文中提取图片引用并替换为占位符。

    Args:
        body_content: 原始 HTML 正文
        rest_base: Confluence REST API 基地址（用于补全相对路径）

    Returns:
        (text_content: str, images_found: list[dict])
        处理后的纯文本内容和提取到的图片列表。
    """
    images_found = []
    img_index = 0

    if not body_content:
        return "", []

    processed_html = body_content

    # Step 1: 提取 <ac:image> 宏（Confluence storage 格式）
    # 格式: <ac:image><ri:attachment ri:filename="photo.png" /></ac:image>
    ac_image_pattern = re.compile(
        r'<ac:image[^>]*>(.*?)</ac:image>', re.IGNORECASE | re.DOTALL
    )

    def _replace_ac_image(match):
        nonlocal img_index
        inner = match.group(1)
        fname_match = re.search(r'ri:filename="([^"]+)"', inner)
        fname = fname_match.group(1) if fname_match else f"image_{img_index}"
        url_match = re.search(r'ri:url="([^"]+)"', inner)
        url = url_match.group(1) if url_match else ""
        alt_match = re.search(r'ac:alt-text[^>]*>([^<]+)', inner)
        alt = alt_match.group(1).strip() if alt_match else fname

        images_found.append({
            "index": img_index,
            "filename": fname,
            "url": url,
            "alt": alt,
            "type": "attachment",
        })
        img_index += 1
        return f"\n[图片: {alt}]\n"

    processed_html = ac_image_pattern.sub(_replace_ac_image, processed_html)

    # Step 2: 提取标准 <img> 标签
    img_tag_pattern = re.compile(r'<img[^>]+>', re.IGNORECASE)

    def _replace_img_tag(match):
        nonlocal img_index
        tag = match.group(0)
        src_match = re.search(r'src\s*=\s*"([^"]*)"', tag)
        src = src_match.group(1) if src_match else ""
        alt_match = re.search(r'alt\s*=\s*"([^"]*)"', tag)
        alt = alt_match.group(1) if alt_match else f"image_{img_index}"

        if src and src.startswith("/"):
            src = f"{rest_base.rstrip('/')}{src}"

        images_found.append({
            "index": img_index,
            "filename": src.split("/")[-1] if src else f"image_{img_index}",
            "url": src,
            "alt": alt,
            "type": "img_tag",
        })
        img_index += 1
        return f"\n![{alt}]({src})\n"

    processed_html = img_tag_pattern.sub(_replace_img_tag, processed_html)

    # Step 3: 表格转 markdown（保留行列结构，避免剥标签后单元格文本粘连成串）
    processed_html = _html_table_to_markdown(processed_html)

    # Step 4: 去 HTML 标签，保留图片标记和 markdown 表格
    text_content = re.sub(r'<[^>]+>', '', processed_html)
    text_content = html.unescape(text_content)  # 表格外残留实体一并还原
    text_content = re.sub(r'\n{3,}', '\n\n', text_content).strip()

    # Step 5: 在末尾附上图片索引
    if images_found:
        text_content += "\n\n---\n### 页面图片\n"
        for img in images_found:
            alt = img["alt"]
            url = img["url"]
            if url:
                text_content += f"- [{alt}]({url})\n"
            else:
                text_content += f"- {alt} (附件: {img['filename']})\n"

    return text_content, images_found


async def confluence_get_page_content(
    params: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """通过 Confluence REST API 获取页面完整内容（含正文 body）。

    与 MCP 的 get_page_mcp_confluence 不同，此工具显式传递
    expand=body.storage,body.view 参数，确保正文被返回。

    Args:
        params:
            page_id: Confluence 页面 ID（必填）
            confluence_base_url: Confluence 实例基地址（可选）
                                如 https://confluence.example.com
                                不传时从 MCP 配置推导
        context: 执行上下文，需包含 _user_id。

    Returns:
        {"type": "document", "success": bool,
         "title": str, "content": str, "metadata": dict, ...}
    """
    page_id = params.get("page_id", "").strip()
    if not page_id:
        return {
            "type": "error",
            "error": "缺少参数: page_id",
            "success": False,
        }

    # 获取用户 MCP 配置
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {
            "type": "error",
            "error": "缺少用户身份信息，无法获取 MCP 配置",
            "success": False,
        }

    try:
        from app.services.settings_service import get_mcp_auth_dict
        mcp_config = await get_mcp_auth_dict(user_id)
    except Exception as e:
        logger.error("Failed to load MCP config for user %s: %s", user_id, e)
        return {
            "type": "error",
            "error": f"加载 MCP 配置失败: {e}",
            "success": False,
        }

    rest_base, username, password = _resolve_confluence_auth(params, mcp_config)
    if not rest_base:
        return {
            "type": "error",
            "error": "Confluence MCP 服务器未配置或缺少 URL 信息。"
                     "请在设置页面的「MCP 服务」中配置 Confluence 服务器，"
                     "或在查询时提供 confluence_base_url 参数",
            "success": False,
        }

    if not username or not password:
        return {
            "type": "error",
            "error": "Confluence MCP 认证信息不完整（缺少用户名或密码），"
                     "请在设置页面检查 Confluence 配置",
            "success": False,
        }

    # 构建 REST API URL — 请求正文内容
    api_url = (
        f"{rest_base}/rest/api/content/{page_id}"
        "?expand=body.storage,body.view,version,space"
    )

    # HTTP Basic Auth
    import base64
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(api_url, headers=headers)
    except httpx.TimeoutException:
        return {
            "type": "error",
            "error": f"Confluence REST API 请求超时（30s），请检查 Confluence 服务状态",
            "success": False,
        }
    except httpx.ConnectError as e:
        return {
            "type": "error",
            "error": f"无法连接到 Confluence 服务器 ({rest_base}): {e}",
            "success": False,
        }
    except Exception as e:
        logger.error("Confluence REST API request failed: %s", e)
        return {
            "type": "error",
            "error": f"Confluence REST API 请求失败: {e}",
            "success": False,
        }

    if resp.status_code == 404:
        # 404 不一定是页面不存在：MCP 代理地址（如 http://10.0.0.1:8112）≠ Confluence
        # REST API 地址（如 https://confluence.example.com），地址推导错误时同样返回 404。
        # 给出可操作的诊断，避免误导 LLM 误判为 pageId 错误。
        return {
            "type": "error",
            "error": (
                f"Confluence 页面获取失败：HTTP 404 (pageId={page_id})。"
                f"请先确认页面是否存在；若页面确实存在，则可能是 Confluence 服务器地址配置错误——"
                f"当前请求的地址为 {rest_base}/rest/api/content/{page_id}，"
                f"请改用 confluence_base_url 参数显式指定真实地址，如 "
                f"confluence_base_url=https://confluence.example.com"
            ),
            "success": False,
        }
    elif resp.status_code == 401 or resp.status_code == 403:
        return {
            "type": "error",
            "error": f"Confluence 认证失败（HTTP {resp.status_code}），请检查 MCP 配置中的用户名和密码",
            "success": False,
        }
    elif resp.status_code != 200:
        return {
            "type": "error",
            "error": f"Confluence REST API 返回错误 (HTTP {resp.status_code})",
            "success": False,
        }

    # 解析响应
    try:
        page_data = resp.json()
    except json.JSONDecodeError:
        return {
            "type": "error",
            "error": "Confluence 返回了非 JSON 响应",
            "success": False,
        }

    title = page_data.get("title", "")
    page_id_str = str(page_data.get("id", page_id))
    space_data = page_data.get("space", {})
    if isinstance(space_data, dict):
        space_key = space_data.get("key", "")
        space_name = space_data.get("name", "")
    else:
        space_key = ""
        space_name = ""

    version_data = page_data.get("version", {})
    if isinstance(version_data, dict):
        version_number = version_data.get("number", "")
        version_when = version_data.get("when", "")
    else:
        version_number = ""
        version_when = ""

    # 提取正文 — 优先 storage 格式（完整 HTML），其次 view 格式（渲染后 HTML）
    body = page_data.get("body", {}) or {}
    body_content = ""
    if isinstance(body, dict):
        body_content = (
            body.get("storage", {}).get("value", "")
            or body.get("view", {}).get("value", "")
            or ""
        )

    # ── 处理正文中的图片 ──
    text_content, images_found = _extract_images_from_html(body_content, rest_base)

    # 太长时截断
    MAX_CONTENT = 15000
    if len(text_content) > MAX_CONTENT:
        text_content = text_content[:MAX_CONTENT] + "\n\n...（内容过长已截断）"

    # 图片信息放入 metadata
    images_meta = []
    for img in images_found:
        entry = {"index": img["index"], "alt": img["alt"], "filename": img["filename"]}
        if img["url"]:
            entry["url"] = img["url"]
        images_meta.append(entry)

    result = {
        "type": "document",
        "success": True,
        "title": title,
        "content": text_content,
        "metadata": {
            "id": page_id_str,
            "space_key": space_key,
            "space_name": space_name,
            "version": version_number,
            "updated": version_when,
            "source": "confluence_rest_api",
            "images": images_meta,
        },
    }

    return result


async def confluence_ocr_page_image(
    params: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """下载 Confluence 页面中的图片附件并 OCR 识别文字。

    用法：先用 confluence_get_page_content 获取页面内容和图片列表，
    然后对感兴趣的图片调用此工具提取图中文字。

    Args:
        params:
            page_id: Confluence 页面 ID（必填）
            filename: 图片附件文件名，如 "diagram.png"（必填，与 confluence_base_url 二选一）
            image_url: 图片直接 URL（与 filename 二选一）
            confluence_base_url: Confluence 实例基地址（可选）
        context: 执行上下文。

    Returns:
        {"type": "document", "success": bool,
         "content": str (OCR 识别文字), "metadata": {...}}
    """
    page_id = params.get("page_id", "").strip()
    filename = params.get("filename", "").strip()
    image_url = params.get("image_url", "").strip()

    if not page_id:
        return {"type": "error", "error": "缺少参数: page_id", "success": False}
    if not filename and not image_url:
        return {
            "type": "error",
            "error": "缺少参数: 请提供 filename（附件名）或 image_url（图片链接）",
            "success": False,
        }

    # 获取 Confluence HTTP 客户端
    http_info = await _get_confluence_http_client(params, context)
    if "error" in http_info:
        return {"type": "error", "error": http_info["error"], "success": False}

    rest_base = http_info["rest_base"]
    headers = http_info["headers"]

    import httpx

    # 确定下载 URL
    download_url = image_url if image_url else ""
    if not download_url and filename:
        # 通过附件 API 获取下载链接
        # 先查附件列表找到匹配的文件
        try:
            att_url = f"{rest_base}/rest/api/content/{page_id}/child/attachment"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(att_url, headers={
                    **headers, "Accept": "application/json",
                })
                if resp.status_code == 200:
                    att_data = resp.json()
                    for att in att_data.get("results", []):
                        att_title = att.get("title", "")
                        if att_title == filename or att_title == f"filename={filename}":
                            dl_link = att.get("_links", {}).get("download", "")
                            if dl_link:
                                if dl_link.startswith("/"):
                                    download_url = f"{rest_base.rstrip('/')}{dl_link}"
                                else:
                                    download_url = dl_link
                                break
        except Exception as e:
            logger.warning("Failed to fetch attachment list: %s", e)

    if not download_url:
        return {
            "type": "error",
            "error": f"无法找到图片下载链接 (filename={filename})",
            "success": False,
        }

    # 下载图片到临时文件
    try:
        # 使用二进制 Accept 下载图片
        img_headers = {k: v for k, v in headers.items() if k.lower() != "accept"}
        img_headers["Accept"] = "image/*"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(download_url, headers=img_headers)

        if resp.status_code != 200:
            return {
                "type": "error",
                "error": f"图片下载失败 (HTTP {resp.status_code}): {download_url}",
                "success": False,
            }

        # 保存到临时文件
        suffix = os.path.splitext(filename or "image.png")[1] or ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

    except httpx.TimeoutException:
        return {"type": "error", "error": "图片下载超时（30s）", "success": False}
    except Exception as e:
        logger.error("Image download failed: %s", e)
        return {"type": "error", "error": f"图片下载失败: {e}", "success": False}

    # OCR 识别
    try:
        from app.tools.tesseract_helper import ocr_image as run_ocr

        # 构建类似 ocr_image 的参数
        ocr_params = {"file_path": tmp_path, "language": "chi_sim+eng"}
        ocr_result = run_ocr(ocr_params)

        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if ocr_result.get("error"):
            return {
                "type": "error",
                "error": f"OCR 识别失败: {ocr_result['error']}",
                "success": False,
            }

        ocr_text = ocr_result.get("content", "")
        return {
            "type": "document",
            "success": True,
            "title": f"图片 OCR: {filename or image_url}",
            "content": ocr_text if ocr_text else "（图片中未识别到文字）",
            "metadata": {
                "source": "confluence_ocr",
                "filename": filename,
                "image_url": download_url,
            },
        }

    except Exception as e:
        logger.error("OCR failed: %s", e)
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return {"type": "error", "error": f"OCR 识别出错: {e}", "success": False}
