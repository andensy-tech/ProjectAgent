"""
Jira-Confluence Report Skill Adapter

通过 subprocess 调用原始 skill 脚本，确保 Web UI 和命令行执行结果完全一致。
"""

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime
from typing import Dict, Any


# 脚本路径（相对于项目根目录）
SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "skills", "jira-confluence-report", "jira_to_confluence_report.py"
)
SKILL_CWD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "skills", "jira-confluence-report"
)


def _parse_output(stdout: str) -> Dict[str, Any]:
    """从脚本 stdout 解析 page_id 和 URL"""
    page_id = None
    page_url = None
    total_issues = 0
    p0_count = 0
    eta_count = 0
    stale_count = 0
    remaining_count = 0

    for line in stdout.splitlines():
        # 解析 page_id: 📄 704114884
        m = re.search(r'📄\s+(\d+)', line)
        if m:
            page_id = m.group(1)

        # 解析 URL: 🔗 https://confluence...
        m = re.search(r'🔗\s+(https?://\S+)', line)
        if m:
            page_url = m.group(1)

        # 解析分类统计: P0: 0, ETA: 20, stale: 3, remaining: 23
        m = re.search(r'P0:\s*(\d+),\s*ETA:\s*(\d+),\s*stale:\s*(\d+),\s*remaining:\s*(\d+)', line)
        if m:
            p0_count = int(m.group(1))
            eta_count = int(m.group(2))
            stale_count = int(m.group(3))
            remaining_count = int(m.group(4))

        # 解析总数: 总计: 46 个Jira
        m = re.search(r'总计:\s*(\d+)\s*个Jira', line)
        if m:
            total_issues = int(m.group(1))

    return {
        "page_id": page_id,
        "page_url": page_url,
        "total_issues": total_issues,
        "p0_count": p0_count,
        "eta_count": eta_count,
        "stale_count": stale_count,
        "remaining_count": remaining_count,
    }


async def jira_confluence_report_handler(params: Dict, context: Dict) -> Dict:
    """
    Handler for jira-confluence-report skill.

    通过 subprocess 调用原始脚本，确保与命令行执行结果完全一致。
    """
    # 构建命令行参数
    cmd = ["python3", SCRIPT_PATH]

    # 如果用户传入了覆盖参数，生成临时 config 文件
    config_overrides = {}
    if "label" in params:
        config_overrides["label"] = params["label"]
    if "epics" in params:
        config_overrides["epics"] = params["epics"]
    if "space_key" in params:
        config_overrides["space_key"] = params["space_key"]
    if "parent_id" in params:
        config_overrides["parent_id"] = params["parent_id"]

    if config_overrides:
        # 生成临时 config 文件
        temp_config = {
            "jira": {},
            "confluence": {},
        }
        if "label" in config_overrides:
            temp_config["jira"]["label"] = config_overrides["label"]
        if "epics" in config_overrides:
            temp_config["jira"]["epics"] = config_overrides["epics"]
        if "space_key" in config_overrides:
            temp_config["confluence"]["space_key"] = config_overrides["space_key"]
        if "parent_id" in config_overrides:
            temp_config["confluence"]["parent_id"] = config_overrides["parent_id"]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=SKILL_CWD
        ) as f:
            import yaml
            yaml.dump(temp_config, f, allow_unicode=True)
            temp_config_path = f.name

        cmd.extend(["--config", os.path.basename(temp_config_path)])
    # 否则使用默认的 config.yaml

    try:
        # 执行脚本
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=SKILL_CWD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=300
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # 清理临时文件
        if config_overrides and "temp_config_path" in locals():
            try:
                os.unlink(temp_config_path)
            except Exception:
                pass

        if proc.returncode != 0:
            return {
                "type": "error",
                "title": "Jira报告生成失败",
                "error": f"脚本退出码 {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}",
                "data": {},
            }

        # 解析输出
        parsed = _parse_output(stdout)

        if not parsed["page_id"]:
            return {
                "type": "error",
                "title": "Jira报告生成失败",
                "error": f"未能从输出中解析页面ID\nstdout:\n{stdout}\nstderr:\n{stderr}",
                "data": {},
            }

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        return {
            "type": "report",
            "title": f"Jira项目管理报告 - {timestamp}",
            "data": {
                "total_issues": parsed["total_issues"],
                "p0_issues": parsed["p0_count"],
                "eta_expiring": parsed["eta_count"],
                "stale_issues": parsed["stale_count"],
                "remaining": parsed["remaining_count"],
            },
            "confluence": {
                "success": True,
                "page_id": parsed["page_id"],
                "url": parsed["page_url"],
                "title": f"Jira项目管理报告 - {timestamp}",
            },
            "actions": [
                {
                    "label": "查看Confluence报告",
                    "url": parsed["page_url"] or "#",
                }
            ],
        }

    except asyncio.TimeoutError:
        return {
            "type": "error",
            "title": "Jira报告生成超时",
            "error": "脚本执行超过 300 秒",
            "data": {},
        }
    except Exception as e:
        return {
            "type": "error",
            "title": "Jira报告生成失败",
            "error": str(e),
            "data": {},
        }
