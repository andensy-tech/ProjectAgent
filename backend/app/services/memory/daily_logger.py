"""
Daily Logger — human-readable Markdown conversation log files.

Writes structured Markdown entries to data/daily_logs/YYYY-MM-DD.md,
one entry per conversation turn. These logs are injected into the
system prompt so the Agent can review recent activity.

Format:
```markdown
# 2026-06-01 Daily Log

---

## Turn 1 [09:30]

**User**: ...

**Tools**: call_mcp_tool(jira/search_issues)

**Assistant**: ...

---
```
"""

import asyncio
import logging
from pathlib import Path
from datetime import date, datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


def _default_log_dir() -> str:
    """Default path for daily log files."""
    return str(Path(__file__).resolve().parent.parent.parent.parent / "data" / "daily_logs")


class DailyLogger:
    """Human-readable daily session logs in Markdown.

    Usage:
        daily_logger.append_entry("2026-06-01", {
            "turn_index": 1,
            "user_message": "...",
            "tools": ["call_mcp_tool(jira/search_issues)"],
            "assistant_summary": "...",
        })
        logs = daily_logger.get_recent_logs(days=2)  # today + yesterday
    """

    def __init__(self, log_dir: Optional[str] = None):
        self.log_dir = log_dir or _default_log_dir()

    def _log_path(self, date_str: str, user_id: Optional[int] = None) -> Path:
        if user_id is not None:
            return Path(self.log_dir) / str(user_id) / f"{date_str}.md"
        return Path(self.log_dir) / f"{date_str}.md"

    def _ensure_dir(self, user_id: Optional[int] = None) -> None:
        if user_id is not None:
            Path(self.log_dir).mkdir(parents=True, exist_ok=True)
            Path(self.log_dir / str(user_id)).mkdir(parents=True, exist_ok=True)
        else:
            Path(self.log_dir).mkdir(parents=True, exist_ok=True)

    async def append_entry(self, date_str: str, entry: Dict[str, Any],
                           user_id: Optional[int] = None) -> None:
        """Append a formatted Markdown entry to the day's log file (async).

        Args:
            date_str: ISO date string (e.g. "2026-06-01")
            entry: Dict with keys:
                - turn_index (int)
                - user_message (str, optional)
                - tools (list[str], optional) — tool call descriptions
                - assistant_summary (str, optional)
            user_id: Optional user ID for per-user log isolation.
                     If provided, logs are written to data/daily_logs/{user_id}/{date_str}.md.
        """
        self._ensure_dir(user_id)
        path = self._log_path(date_str, user_id)

        # Build entry (string ops are cheap, do in current thread)
        content = self._format_entry(date_str, entry)

        # File I/O in thread pool to avoid blocking async event loop
        await asyncio.to_thread(self._sync_write_log, path, content, date_str)

        logger.debug("Appended entry to daily log: %s turn %d", date_str, entry.get("turn_index", 0))

    @staticmethod
    def _format_entry(date_str: str, entry: Dict[str, Any]) -> str:
        """构建单条日志条目文本（同步，纯字符串操作）。"""
        lines = []
        turn = entry.get("turn_index", 1)
        now = datetime.now().strftime("%H:%M")
        lines.append(f"## Turn {turn} [{now}]")
        lines.append("")

        user_msg = entry.get("user_message", "")
        if user_msg:
            lines.append(f"**User**: {user_msg}")
            lines.append("")

        tools = entry.get("tools", [])
        if tools:
            tool_str = ", ".join(tools)
            lines.append(f"**Tools**: {tool_str}")
            lines.append("")

        assistant = entry.get("assistant_summary", "")
        if assistant:
            lines.append(f"**Assistant**: {assistant}")
            lines.append("")

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _sync_write_log(path: Path, content: str, date_str: str) -> None:
        """同步写入日志文件（在 asyncio.to_thread 中运行）。"""
        if path.exists() and path.stat().st_size > 0:
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
        else:
            header = f"# {date_str} Daily Log\n\n---\n\n"
            with open(path, "w", encoding="utf-8") as f:
                f.write(header + content)

    def read_log(self, date_str: str, user_id: Optional[int] = None) -> Optional[str]:
        """Read an entire day's log file. Returns None if not found."""
        path = self._log_path(date_str, user_id)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read daily log %s: %s", date_str, e)
            return None

    def get_recent_logs(self, days: int = 2, user_id: Optional[int] = None) -> Optional[str]:
        """Read recent daily logs (today and previous days) as one combined string.

        Args:
            days: Number of days to include (default 2 = today + yesterday)
            user_id: Optional user ID for per-user log isolation.

        Returns:
            Combined Markdown string, or None if no logs found.
        """
        from datetime import timedelta

        today = date.today()
        parts = []
        for i in range(days):
            d = today - timedelta(days=i)
            date_str = d.isoformat()
            log = self.read_log(date_str, user_id)
            if log:
                parts.append(log)

        if not parts:
            return None

        return "\n\n".join(parts)


# Global singleton
daily_logger = DailyLogger()
