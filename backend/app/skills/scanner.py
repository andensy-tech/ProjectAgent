"""
Skill Scanner - Dynamic directory scanning for script-based skills.

Convention-based discovery (no skill.json required):
  - Directory name → skill name
  - main.py (or unique .py file) → entry_point
  - Module docstring → description
  - Trigger → chat, Timeout → 300s
  - Keywords → auto-derived from description

Optional skill.json overrides any convention-derived field.
"""

import json
import os
import re
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ProjectAgent/skills/ directory, resolved relative to this file:
# backend/app/skills/scanner.py  -> ../../../
SKILLS_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "skills"
))

SKILL_JSON_FILENAME = "skill.json"

# Entry point candidates in priority order
ENTRY_POINT_CANDIDATES = ["main.py", "run.py"]


@dataclass
class SkillDef:
    """Definition of a discovered skill."""
    name: str
    display_name: str
    description: str
    version: str = "0.0.0"
    handler_type: str = "script"  # "script" | "registered"
    directory: str = ""           # full path to skill directory
    entry_point: Optional[str] = None   # filename relative to directory
    entry_args: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=lambda: ["chat"])
    timeout: int = 300  # seconds
    mcp_required: List[str] = field(default_factory=list)

    @property
    def entry_point_path(self) -> Optional[str]:
        """Full path to entry point script."""
        if self.entry_point:
            return os.path.join(self.directory, self.entry_point)
        return None

    def is_script_skill(self) -> bool:
        """Whether this skill runs via subprocess."""
        return self.handler_type == "script" and self.entry_point is not None


class SkillScanner:
    """Scans the skills directory for discoverable skills."""

    def __init__(self, skills_dir: str = None):
        self._skills_dir = skills_dir or SKILLS_DIR
        self._cache: Optional[List[SkillDef]] = None

    def scan(self, force: bool = False) -> List[SkillDef]:
        """Scan skills directory and return all discovered skills (cached)."""
        if self._cache is not None and not force:
            return self._cache
        self._cache = self._do_scan()
        return self._cache

    def get_metadata(self) -> Dict[str, SkillDef]:
        """Return dict of {name: SkillDef} for all discovered skills."""
        return {s.name: s for s in self.scan()}

    def _do_scan(self) -> List[SkillDef]:
        """Walk the directory and discover skills by convention + optional skill.json."""
        if not os.path.isdir(self._skills_dir):
            logger.warning("Skills directory not found: %s", self._skills_dir)
            return []

        skills: List[SkillDef] = []
        seen_names: set = set()

        for entry in sorted(os.listdir(self._skills_dir)):
            skill_dir = os.path.join(self._skills_dir, entry)
            if not os.path.isdir(skill_dir) or entry.startswith("."):
                continue

            # Step 1: derive base definition from conventions
            skill_def = self._convention_discover(skill_dir, entry)
            if skill_def is None:
                # Conventions didn't match; maybe skill.json defines it
                skill_def = self._json_only_discover(skill_dir, entry)
            if skill_def is None:
                logger.debug("Skipping %s (no entry point and no skill.json)", entry)
                continue

            # Step 2: override with skill.json if present
            skill_def = self._apply_json_overrides(skill_def)

            # Step 3: validate
            if skill_def.handler_type == "script" and not skill_def.entry_point:
                logger.debug("Skipping %s (script type but no entry point)", entry)
                continue

            if skill_def.name in seen_names:
                logger.warning("Duplicate skill name '%s' in %s, skipping", skill_def.name, entry)
                continue

            seen_names.add(skill_def.name)
            skills.append(skill_def)
            logger.info("Discovered skill: %s (type=%s)", skill_def.name, skill_def.handler_type)

        return skills

    def _convention_discover(self, skill_dir: str, dir_name: str) -> Optional[SkillDef]:
        """Derive skill definition from directory conventions."""
        entry_point = self._find_entry_point(skill_dir, dir_name)
        if not entry_point:
            return None

        description = self._extract_docstring(skill_dir, entry_point)
        keywords = self._keywords_from_description(description) if description else []

        return SkillDef(
            name=dir_name,
            display_name=dir_name,
            description=description or "",
            directory=skill_dir,
            entry_point=entry_point,
            keywords=keywords,
        )

    def _find_entry_point(self, skill_dir: str, dir_name: str) -> Optional[str]:
        """Find the entry point script by convention."""
        # Priority 1: main.py or run.py
        for candidate in ENTRY_POINT_CANDIDATES:
            if os.path.isfile(os.path.join(skill_dir, candidate)):
                return candidate

        # Priority 2: file matching directory name
        dir_py = f"{dir_name}.py"
        if os.path.isfile(os.path.join(skill_dir, dir_py)):
            return dir_py

        # Priority 3: single .py file in directory
        py_files = [f for f in os.listdir(skill_dir)
                     if f.endswith(".py") and os.path.isfile(os.path.join(skill_dir, f))]
        if len(py_files) == 1:
            return py_files[0]

        return None

    def _extract_docstring(self, skill_dir: str, entry_point: str) -> Optional[str]:
        """Extract first line of module docstring as description."""
        try:
            with open(os.path.join(skill_dir, entry_point), "r", encoding="utf-8") as f:
                content = f.read(4096)  # Read enough for docstring (increased from 1024)
            # Match triple-quoted string right after #! or imports
            m = re.search(r'"""(.*?)"""', content, re.DOTALL)
            if m:
                desc = m.group(1).strip().split("\n")[0].strip()
                return desc[:200] if desc else None
            m = re.search(r"'''(.*?)'''", content, re.DOTALL)
            if m:
                desc = m.group(1).strip().split("\n")[0].strip()
                return desc[:200] if desc else None
        except (IOError, UnicodeDecodeError):
            pass
        return None

    def _keywords_from_description(self, description: str) -> List[str]:
        """Derive simple keywords from description."""
        # Extract meaningful Chinese/English words from first line
        words = re.findall(r'[\w]+', description)
        # Filter short/common words
        return [w.lower() for w in words if len(w) > 1][:10]

    def _json_only_discover(self, skill_dir: str, dir_name: str) -> Optional[SkillDef]:
        """Discover a skill that relies entirely on skill.json."""
        skill_json = os.path.join(skill_dir, SKILL_JSON_FILENAME)
        if not os.path.isfile(skill_json):
            return None
        # Minimal stub; _apply_json_overrides will fill it in
        return SkillDef(
            name=dir_name,
            display_name=dir_name,
            description="",
            directory=skill_dir,
        )

    def _apply_json_overrides(self, skill_def: SkillDef) -> SkillDef:
        """Merge optional skill.json overrides onto a convention-derived SkillDef."""
        json_path = os.path.join(skill_def.directory, SKILL_JSON_FILENAME)
        if not os.path.isfile(json_path):
            return skill_def

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                overrides = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to read %s: %s", json_path, e)
            return skill_def

        # Name is always from directory path, not overrideable
        # (prevents confusion when directory and name mismatch)
        import copy
        merged = copy.deepcopy(skill_def)
        merged.display_name = overrides.get("display_name", merged.display_name)
        merged.description = overrides.get("description", merged.description)
        merged.version = overrides.get("version", merged.version)
        merged.handler_type = overrides.get("handler_type", merged.handler_type)
        # Registered skills have no entry point
        if merged.handler_type == "registered":
            merged.entry_point = None
        else:
            merged.entry_point = overrides.get("entry_point", merged.entry_point)
        merged.entry_args = overrides.get("entry_args", merged.entry_args)
        merged.keywords = overrides.get("keywords", merged.keywords)
        merged.triggers = overrides.get("triggers", merged.triggers)
        merged.timeout = int(overrides.get("timeout", merged.timeout))
        merged.mcp_required = overrides.get("mcp_required", merged.mcp_required)

        return merged


# Global scanner instance
skill_scanner = SkillScanner()
