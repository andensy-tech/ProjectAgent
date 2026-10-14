#!/usr/bin/env python3
"""
Test program: verify Jira query results are formatted as proper markdown tables.

Simulates the full pipeline:
  1. Mock MCP returns raw Jira JSON data (as a real MCP server would)
  2. AgentEngine._format_tool_result formats it as markdown tables
  3. _sanitize_markdown_tables cleans up any artifacts
  4. Outputs an HTML file showing the final rendered result

Run:  cd backend && source venv/bin/activate && python tests/test_table_format.py
View: open test_jira_format_output.html
"""

import json
import re
import sys
import os
from typing import Any, Dict, List

# Ensure we can import from the project
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.agent.engine import AgentEngine

engine = AgentEngine()
OUTPUT_HTML = "test_jira_format_output.html"


# ── Simulated MCP Jira responses ─────────────────────────────────────────────

JIRA_ISSUES_RAW = [
    {
        "key": "TV-123456",
        "summary": "[60001][OEM][CHIP-X][R+][14.0]:Settings下面的国家和语言列表都需要能做到ini可自由配置",
        "status": "In Progress",
        "assignee": {"displayName": "User B"},
    },
    {
        "key": "TV-123456",
        "summary": "[60001][OEM][CHIP-X][R+][14.0]:Wizard下面的国家和语言列表都需要能做到ini可自由配置",
        "status": "To Do",
        "assignee": {"displayName": "User C"},
    },
    {
        "key": "TV-123456",
        "summary": "[OEM-A][CHIP-X][R+][14.0]:示例 issue 标题：开机向导页面支持配置 logo 图片路径",
        "status": "To Do",
        "assignee": {"displayName": "User A"},
    },
    {
        "key": "TV-123456",
        "summary": "[OEM-A][CHIP-X][R+][14.0]:示例 issue 标题：开机向导页面支持配置 logo 图片路径",
        "status": "To Do",
        "assignee": {"displayName": "User B"},
    },
]

LABELS_ISSUES_RAW = [
    {
        "key": "TV-123456",
        "summary": "动态背光接口适配API设计",
        "status": "In Progress",
        "assignee": {"displayName": "User D"},
    },
    {
        "key": "TV-123456",
        "summary": "Multiscreen投屏协议适配",
        "status": "Open",
        "assignee": {"displayName": "User E"},
    },
    {
        "key": "TV-123456",
        "summary": "动态背光HAL层实现",
        "status": "Code Review",
        "assignee": {"displayName": "User D"},
    },
    {
        "key": "TV-123456",
        "summary": "HDR画质参数自适应调节",
        "status": "To Do",
        "assignee": {"displayName": "User F"},
    },
    {
        "key": "TV-123456",
        "summary": "串口调试命令扩展支持",
        "status": "Closed",
        "assignee": {"displayName": "User B"},
    },
]


# ── Simulate LLM final response (what the LLM would produce given the tool results) ──

def simulate_llm_response(jira_table: str, labels_table: str) -> str:
    """Simulate what the LLM would produce given pre-formatted tool results.

    Since the tool results are already perfect markdown tables, the LLM just
    wraps them with commentary. This is the text that would go through
    _sanitize_markdown_tables and be sent to the frontend.
    """
    lines = []
    lines.append("## 本周待办 Jira (4 个)")
    lines.append("")
    lines.append(jira_table)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## labels=oem_release_api (5 个)")
    lines.append("")
    lines.append("以下是相关 Jira 的详细列表：")
    lines.append("")
    lines.append(labels_table)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 总结")
    lines.append("")
    lines.append("- **本周待办共 4 个 Jira**：2 个开机向导小logo需求，2 个国家和语言列表ini配置需求")
    lines.append("- **labels=oem_release_api 共 5 个**：涵盖动态背光、Multiscreen投屏、HDR画质等")
    lines.append("- 建议重点关注 **动态背光接口适配 (TV-924502)** 和 **HAL层实现 (TV-924504)**，两者关联性较强")
    return "\n".join(lines)


# ── Generate HTML with rendered output ──

def generate_html(raw_markdown: str) -> str:
    """Generate a self-contained HTML page that renders the markdown with a client-side library.

    Uses showdown.js (CDN) to render GFM markdown, similar to the frontend's
    ReactMarkdown + remarkGfm.
    """
    # Escape the markdown for embedding in a JS string
    md_json = json.dumps(raw_markdown)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jira 查询结果格式验证</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/showdown/2.1.0/showdown.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: #f5f5f5;
    color: #333;
    padding: 20px;
    line-height: 1.6;
  }}
  .container {{
    max-width: 960px;
    margin: 0 auto;
  }}
  h1 {{
    font-size: 22px;
    margin-bottom: 20px;
    color: #1a1a1a;
  }}
  .chat-bubble {{
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    padding: 20px 24px;
    margin-bottom: 30px;
  }}
  .chat-bubble h2 {{
    font-size: 16px;
    color: #666;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid #eee;
  }}
  .rendered-content {{
    font-size: 14px;
  }}
  /* ── Table styling (matching ChatPage.tsx) ── */
  .rendered-content table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 13px;
    display: table;
    border-spacing: 0;
    border: 1px solid #e8e8e8;
    border-radius: 6px;
    overflow: hidden;
  }}
  .rendered-content table thead {{
    background: #fafafa;
  }}
  .rendered-content table th,
  .rendered-content table td {{
    padding: 8px 12px;
    text-align: left;
    border-bottom: 1px solid #f0f0f0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 200px;
  }}
  .rendered-content table thead th {{
    font-weight: 600;
    color: #333;
    border-bottom: 2px solid #e8e8e8;
  }}
  .rendered-content table tr:nth-child(even) td {{
    background-color: #fafafc;
  }}
  .rendered-content table tr:hover td {{
    background-color: #f0f4ff;
  }}
  .rendered-content th:nth-child(3),
  .rendered-content td:nth-child(3) {{
    text-align: center;
  }}
  .rendered-content th:nth-child(4),
  .rendered-content td:nth-child(4) {{
    text-align: center;
  }}
  .rendered-content h2 {{ margin-top: 0; }}
  .rendered-content h3 {{ margin: 16px 0 8px; font-size: 15px; }}
  .rendered-content hr {{ margin: 20px 0; border: none; border-top: 1px solid #e0e0e0; }}
  .rendered-content ul {{ padding-left: 20px; }}
  .rendered-content li {{ margin-bottom: 4px; }}

  .raw-section {{
    background: #1e1e2e;
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 30px;
  }}
  .raw-section h2 {{
    font-size: 14px;
    color: #aaa;
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .raw-section pre {{
    color: #cdd6f4;
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
    font-size: 13px;
    line-height: 1.5;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  .badge {{
    display: inline-block;
    background: #4A90E2;
    color: #fff;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    margin-left: 8px;
    vertical-align: middle;
  }}
  .status-bar {{
    display: flex;
    gap: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }}
  .status-item {{
    background: #fff;
    border-radius: 8px;
    padding: 12px 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    flex: 1;
    min-width: 120px;
  }}
  .status-item .label {{
    font-size: 11px;
    color: #999;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .status-item .value {{
    font-size: 18px;
    font-weight: 600;
    color: #333;
    margin-top: 2px;
  }}
  .status-item .value.pass {{ color: #22c55e; }}
  .status-item .value.fail {{ color: #ef4444; }}
  .check {{ color: #22c55e; }}
  .cross {{ color: #ef4444; }}
</style>
</head>
<body>
<div class="container">
  <h1>🔍 Jira 查询结果格式验证</h1>
  <p style="color:#666;margin-bottom:20px;font-size:14px;">
    模拟完整的 Agent 工具结果格式化 → LLM 回应 → 前端渲染链路
  </p>

  <div class="status-bar" id="statusBar"></div>

  <div class="chat-bubble">
    <h2>最终显示效果（模拟前端聊天气泡）</h2>
    <div class="rendered-content" id="renderedOutput"></div>
  </div>

  <div class="raw-section">
    <h2>原始 Markdown（LLM 生成的回应文本）</h2>
    <pre id="rawOutput"></pre>
  </div>
</div>

<script>
  (function() {{
    const converter = new showdown.Converter({{
      tables: true,
      strikethrough: true,
      tasklists: true,
      simpleLineBreaks: false,
      openLinksInNewWindow: true,
      noHeaderId: true,
    }});
    converter.setFlavor('github');

    const md = {md_json};

    // Render
    const html = converter.makeHtml(md);
    document.getElementById('renderedOutput').innerHTML = html;
    document.getElementById('rawOutput').textContent = md;

    // Validation checks
    const checks = [
      {{ label: '表格生成', test: md.includes('|---') && md.includes('| Issue |') }},
      {{ label: '列数 ≤ 4', test: checkColumnCount(md) }},
      {{ label: '长文本截断', test: md.includes('...') }},
      {{ label: '无孤立分隔线', test: !/^\\\|[\\s\\-:|]+\\\|$/m.test(md.replace(/\\| Issue \\|.*/, '')) }},
      {{ label: '标题分层', test: md.includes('## ') }},
      {{ label: '区块分隔', test: md.includes('---') }},
    ];

    function checkColumnCount(md) {{
      const headerMatch = md.match(/^\\| [^|]+ \\|$/m);
      if (!headerMatch) return false;
      return headerMatch[0].split('|').length - 1 <= 4;
    }}

    const statusBar = document.getElementById('statusBar');
    checks.forEach(c => {{
      const el = document.createElement('div');
      el.className = 'status-item';
      el.innerHTML = `
        <div class="label">${{c.test ? '✓' : '✗'}} ${{c.label}}</div>
        <div class="value ${{c.test ? 'pass' : 'fail'}}">${{c.test ? '通过' : '未通过'}}</div>
      `;
      statusBar.appendChild(el);
    }});
  }})();
</script>
</body>
</html>
"""


# ── Main ──

def main():
    # Step 1: Format tool results as markdown tables
    print("=" * 60)
    print("  测试: Jira 查询结果表格格式化")
    print("=" * 60)

    # Test 1: format_tool_result with dict wrapper (like real MCP response)
    print("\n[1/5] 格式化 Jira 列表 (dict wrapper)...")
    jira_dict = {"issues": JIRA_ISSUES_RAW, "total": len(JIRA_ISSUES_RAW)}
    result1 = engine._format_tool_result(jira_dict)
    print("  PASS ✓" if "| Issue |" in result1 else "  FAIL ✗")
    print(result1[:200])

    # Test 2: format_tool_result with direct list
    print("\n[2/5] 格式化 Jira 列表 (直接数组)...")
    result2 = engine._format_tool_result(JIRA_ISSUES_RAW)
    print("  PASS ✓" if "| Issue |" in result2 else "  FAIL ✗")

    # Test 3: format labels Jira list
    print("\n[3/5] 格式化 labels=oem_release_api 结果...")
    labels_dict = {"issues": LABELS_ISSUES_RAW, "total": len(LABELS_ISSUES_RAW)}
    result3 = engine._format_tool_result(labels_dict)
    print("  PASS ✓" if "| Issue |" in result3 else "  FAIL ✗")

    # Test 4: Simulate full LLM response (tool results → LLM → final markdown)
    print("\n[4/5] 模拟 LLM 完整回应...")
    llm_response = simulate_llm_response(result1, result3)
    sanitized = engine._sanitize_markdown_tables(llm_response)
    print(f"  原始长度: {len(llm_response)}, 清理后长度: {len(sanitized)}")

    # Test 5: Check no orphaned separator lines persist
    print("\n[5/5] 孤立分隔线检测...")
    import re
    lines = sanitized.split("\n")
    orphaned_found = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^\|[\s\-:|]+\|$', stripped):
            # Check if prev non-blank line is a valid header (starts with |, not just dashes/colons)
            prev_idx = i - 1
            while prev_idx >= 0 and not lines[prev_idx].strip():
                prev_idx -= 1
            prev_line = lines[prev_idx].strip() if prev_idx >= 0 else ""
            has_header = (
                prev_line.startswith("|") and prev_line.endswith("|")
                and not re.match(r'^\|[\s\-:|]+\|$', prev_line)
            )
            # Check if next non-blank line is a valid data row
            next_idx = i + 1
            while next_idx < len(lines) and not lines[next_idx].strip():
                next_idx += 1
            next_line = lines[next_idx].strip() if next_idx < len(lines) else ""
            has_data = (
                next_line.startswith("|") and next_line.endswith("|")
                and not re.match(r'^\|[\s\-:|]+\|$', next_line)
            )
            if not (has_header and has_data):
                orphaned_found.append(stripped)
    if orphaned_found:
        print(f"  FAIL ✗ 发现 {len(orphaned_found)} 条孤立分隔线:")
        for o in orphaned_found:
            print(f"    {repr(o)}")
    else:
        print("  PASS ✓ 无孤立分隔线")

    # Test 6: Sanitize function handles bad LLM output with orphaned separators
    print("\n[6/6] 错误格式净化测试（模拟 LLM 生成错误表格）...")
    bad_llm_output = (
        "Here are the Jira issues:\n\n"
        "| -------|------|:----:|--------|:------:|\n"
        "| Issue | 摘要 | 状态 | 负责人 | "
        "|-------|------|:----:|:------:| "
        "| TV-925371 | Settings ini配置开发 | Open | User B | "
        "| TV-926361 | Wizard ini配置开发 | To Do | User C |\n\n"
        "Also there were some results:\n\n"
        "| -------|------|:----:|--------|:------:|\n"
    )
    cleaned = engine._sanitize_markdown_tables(bad_llm_output)

    # Verify: valid table preserved
    has_valid_table = "| Issue | 摘要 | 状态 | 负责人 |\n|-------|------|:----:|:------:|\n| TV-925371" in cleaned
    # Verify: orphaned separators removed
    orphaned_in_cleaned = re.findall(r'^\|[\s\-:|]+\|$', cleaned, re.MULTILINE)
    orphaned_count = 0
    for sep in orphaned_in_cleaned:
        lines_list = cleaned.split("\n")
        sep_idx = lines_list.index(sep) if sep in lines_list else -1
        if sep_idx >= 0:
            prev = lines_list[sep_idx - 1].strip() if sep_idx > 0 else ""
            nxt = lines_list[sep_idx + 1].strip() if sep_idx < len(lines_list) - 1 else ""
            if not (prev.startswith("|") and nxt.startswith("|")):
                orphaned_count += 1
    no_orphaned = orphaned_count == 0

    if has_valid_table and no_orphaned:
        print("  PASS ✓ 净化后表格保留完整，孤立分隔线已清除")
    elif not has_valid_table:
        print("  FAIL ✗ 净化后有效表格被破坏")
    else:
        print(f"  FAIL ✗ 仍有 {orphaned_count} 条孤立分隔线未清除")

    # Generate HTML
    print(f"\n{'=' * 60}")
    print(f"  生成 HTML 验证页面...")
    html_content = generate_html(sanitized)
    output_path = os.path.join(os.path.dirname(__file__), "..", OUTPUT_HTML)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"  ✓ 已输出: {output_path}")
    print(f"    请在浏览器中打开此 HTML 文件查看渲染效果")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
