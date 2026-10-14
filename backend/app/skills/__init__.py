"""
Skill adapters for Project Agent.

These modules wrap the original skill scripts to provide async interfaces
that integrate with the backend's MCPManager and ActionExecutor.

Also provides SkillScanner for dynamic discovery and ScriptSkillRunner
for standardized subprocess execution of script-based skills.
"""

from .jira_report_skill import jira_confluence_report_handler
from .jira_cl_monitor_skill import jira_cl_merge_monitor_handler
from .scanner import skill_scanner, SkillDef, SkillScanner
from .script_runner import make_script_handler, run_script_skill

__all__ = [
    "jira_confluence_report_handler",
    "jira_cl_merge_monitor_handler",
    "skill_scanner",
    "SkillDef",
    "SkillScanner",
    "make_script_handler",
    "run_script_skill",
]
