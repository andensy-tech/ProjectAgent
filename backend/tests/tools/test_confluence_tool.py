"""
组件测试：Confluence 读取工具的纯逻辑部分（app/tools/confluence_tool.py）。

用户场景：web chat 读取 Confluence 页面内容（含表格列、图片）。认证解析、
HTML 表格 → markdown、图片提取是读取流程的关键前置逻辑，用真实生产函数验证。
"""

from app.tools.confluence_tool import (
    _extract_images_from_html,
    _html_table_to_markdown,
    _resolve_confluence_auth,
)


class TestResolveConfluenceAuth:
    """从参数/用户 MCP 配置提取 Confluence REST 认证。"""

    def test_param_base_url_strips_path_suffix(self):
        # 用户直接粘贴完整页面 URL → 剥离 /pages 路径，得到 REST 基地址
        params = {"confluence_base_url": "https://confluence.example.com/pages/viewpage.action?pageId=734329354"}
        rest_base, username, password = _resolve_confluence_auth(params, {})
        assert rest_base == "https://confluence.example.com"

    def test_mcp_config_fallback(self):
        params = {}
        mcp_config = {
            "confluence": {
                "url": "http://10.0.0.1:8112/mcp",
                "auth": {"username": "test.user", "password": "p@ss"},
            },
        }
        rest_base, username, password = _resolve_confluence_auth(params, mcp_config)
        # MCP URL 剥离 /mcp 后缀作为 REST 基地址
        assert rest_base == "http://10.0.0.1:8112"
        assert username == "test.user"
        assert password == "p@ss"

    def test_server_url_preferred(self):
        params = {}
        mcp_config = {
            "confluence": {
                "url": "http://10.0.0.1:8112/mcp",
                "auth": {"server_url": "https://confluence.example.com", "username": "u", "password": "p"},
            },
        }
        rest_base, username, password = _resolve_confluence_auth(params, mcp_config)
        assert rest_base == "https://confluence.example.com"

    def test_no_config(self):
        rest_base, username, password = _resolve_confluence_auth({}, {})
        assert (rest_base, username, password) == ("", "", "")


class TestHtmlTableToMarkdown:
    """Confluence storage 格式表格 → markdown 表格（用户场景：提取表格列）。"""

    def test_basic_table(self):
        html = "<table><tr><th>项目</th><th>状态</th></tr><tr><td>AML123</td><td>进行中</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "| 项目 | 状态 |" in md
        assert "| AML123 | 进行中 |" in md

    def test_cell_entities_unescaped(self):
        html = "<table><tr><td>A &amp; B</td><td>1 &lt; 2</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "A & B" in md
        assert "1 < 2" in md

    def test_pipe_escaped_in_cell(self):
        html = "<table><tr><td>a|b</td></tr></table>"
        md = _html_table_to_markdown(html)
        assert "a\\|b" in md

    def test_no_table_returns_unchanged(self):
        text = "plain text without table"
        assert _html_table_to_markdown(text) == text


class TestExtractImagesFromHtml:
    """Confluence 图片提取：ac:image 宏 + 普通 img。"""

    def test_ac_image_extracted(self):
        html = '<ac:image><ri:attachment ri:filename="photo.png" /></ac:image>'
        text, images = _extract_images_from_html(html, "http://confluence")
        assert images  # 提取到图片
        assert len(images) >= 1
        assert images[0]["filename"] == "photo.png"

    def test_plain_img_extracted(self):
        html = '<img src="/download/attachments/123/a.png" alt="a">'
        text, images = _extract_images_from_html(html, "http://confluence")
        assert images
        assert images[0]["filename"] == "a.png"

    def test_empty_content(self):
        text, images = _extract_images_from_html("", "http://confluence")
        assert text == ""
        assert images == []
