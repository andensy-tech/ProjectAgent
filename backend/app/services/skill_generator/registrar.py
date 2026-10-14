"""
Skill Registrar — dynamically registers generated skills into the runtime.

Registration flow:
1. Write main.py to skills/{name}/ directory
2. Create handler via make_script_handler
3. Register with ActionExecutor
4. Update SkillScanner cache
5. Update MemorySystem
"""

import os
import logging
from typing import Optional
from app.services.skill_generator.generator import SKILLS_DIR
from app.skills.scanner import SkillDef
from app.services.memory import memory_system

logger = logging.getLogger(__name__)


async def register_skill(
    skill_name: str,
    code: str,
    description: str = "",
) -> Optional[SkillDef]:
    """Register a newly generated skill into the runtime.

    Args:
        skill_name: Kebab-case name for the skill directory.
        code: Generated Python source code.
        description: One-line description of the skill.

    Returns:
        SkillDef on success, None on failure.
    """
    try:
        # 1. Create skill directory
        skill_dir = os.path.join(SKILLS_DIR, skill_name)
        os.makedirs(skill_dir, exist_ok=True)

        # 2. Write main.py
        main_py_path = os.path.join(skill_dir, "main.py")
        with open(main_py_path, "w", encoding="utf-8") as f:
            f.write(code)
        logger.info("Written skill file: %s", main_py_path)

        # 3. Build SkillDef
        skill_def = SkillDef(
            name=skill_name,
            display_name=skill_name,
            description=description or "Auto-generated skill",
            directory=skill_dir,
            entry_point="main.py",
        )

        # 4. Register with ActionExecutor
        from app.services.agent.executor import action_executor
        from app.skills.script_runner import make_script_handler

        handler = make_script_handler(skill_def)
        action_executor.register_skill(skill_name, handler)
        logger.info("Registered skill handler: %s", skill_name)

        # 5. Update SkillScanner cache
        from app.skills.scanner import skill_scanner
        scanner_cache = getattr(skill_scanner, "_cache", None)
        if scanner_cache is not None:
            # Replace existing or append
            for i, s in enumerate(scanner_cache):
                if s.name == skill_name:
                    scanner_cache[i] = skill_def
                    break
            else:
                scanner_cache.append(skill_def)
            logger.info("Updated SkillScanner cache: %s", skill_name)

        # 6. Update MemorySystem
        memory_system.system_memory.add_skill(
            name=skill_name,
            description=description or "Auto-generated skill",
            params=[],
        )
        logger.info("Updated MemorySystem: %s", skill_name)

        return skill_def

    except Exception as e:
        logger.error("Failed to register skill '%s': %s", skill_name, e, exc_info=True)
        return None
