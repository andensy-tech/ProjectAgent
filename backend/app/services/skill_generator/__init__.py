"""
Skill Generator — enables the Agent to create new skills at runtime.

Capabilities:
- template: Python boilerplate for standalone skills
- generator: LLM-driven code generation
- validator: syntax + protocol validation
- registrar: dynamic registration into executor + memory + scanner
"""

from app.services.skill_generator.generator import SkillGenerator
from app.services.skill_generator.validator import validate_skill, ValidationResult
from app.services.skill_generator.registrar import register_skill

# Global singleton
skill_generator = SkillGenerator()

__all__ = [
    "skill_generator",
    "SkillGenerator",
    "validate_skill",
    "register_skill",
    "ValidationResult",
]
