"""
Skill Generator — LLM-driven code generation for new skills.

Uses the "skill_engineer" system prompt to generate standalone Python
scripts that follow the stdin/stdout JSON protocol. Supports iterative
fix cycles when validation fails.
"""

import os
import logging
from typing import Optional
from app.services.llm import llm_router, TaskType, prompt_manager
from app.services.skill_generator.template import SKILL_GENERATION_SYSTEM_PROMPT
from app.services.skill_generator.validator import validate_skill

logger = logging.getLogger(__name__)

# Skills directory (ProjectAgent/skills/)
SKILLS_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "skills"
))


class SkillGenerator:
    """Generates new skills via LLM code generation."""

    MAX_RETRIES = 2

    async def generate_skill(
        self,
        skill_name: str,
        description: str,
        requirements: str,
        parameters: list,
        dependencies: str = "none",
        output_format: str = "",
        default_provider: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a skill main.py file by calling the LLM.

        Args:
            skill_name: Kebab-case name for the skill.
            description: One-line description.
            requirements: Detailed requirements for the LLM to implement.
            parameters: List of param dicts with name/type/description.
            dependencies: Required pip packages or "none".
            output_format: Description of expected output data.

        Returns:
            Generated Python source code as string, or None on failure.
        """
        prompt = prompt_manager.format_prompt(
            "skill_generation",
            skill_name=skill_name,
            skill_description=description,
            skill_requirements=requirements,
            parameters=str(parameters),
            dependencies=dependencies,
            output_format=output_format or "Standard success/error response",
        )

        messages = [
            {"role": "system", "content": SKILL_GENERATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        for attempt in range(1 + self.MAX_RETRIES):
            try:
                code = await llm_router.route_and_complete(
                    task_type=TaskType.REQUIREMENT_UNDERSTANDING,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=4096,
                    default_provider=default_provider,
                )
            except Exception as e:
                logger.error("Skill generation LLM call failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES:
                    continue
                return None

            code = self._clean_code(code)
            if not code:
                if attempt < self.MAX_RETRIES:
                    messages.append({"role": "user", "content": "The previous response contained no valid Python code. Please output ONLY the Python code, no explanation."})
                    continue
                return None

            # Validate
            valid, errors = await validate_skill(code, skill_name)
            if valid:
                return code

            logger.warning("Skill validation failed (attempt %d): %s", attempt + 1, errors)
            if attempt < self.MAX_RETRIES:
                messages.append({
                    "role": "user",
                    "content": f"The generated code has errors:\n{chr(10).join(errors)}\n\nPlease fix these issues and output only the corrected Python code.",
                })
            else:
                # Return best attempt even if invalid — caller can decide
                return code

        return None

    async def generate_skill_design(
        self,
        user_request: str,
        action: str,
        entities: dict,
        context: str = "",
        default_provider: Optional[str] = None,
    ) -> dict:
        """Design a new skill specification using LLM.

        Returns a dict with: skill_name, skill_description, skill_requirements,
        parameters, external_dependencies, output_format.
        """
        prompt = prompt_manager.format_prompt(
            "skill_design",
            user_request=user_request,
            action=action,
            entities=str(entities),
            context=context,
        )

        messages = prompt_manager.build_messages(
            system_role="skill_engineer",
            user_prompt=prompt,
        )

        try:
            result = await llm_router.route_and_complete_structured(
                task_type=TaskType.REQUIREMENT_UNDERSTANDING,
                messages=messages,
                default_provider=default_provider,
            )
            return result
        except Exception as e:
            logger.error("Skill design LLM call failed: %s", e)
            # Minimal fallback design
            return {
                "skill_name": "custom-task",
                "skill_description": action or user_request[:100],
                "skill_requirements": user_request,
                "parameters": [],
                "external_dependencies": "none",
                "output_format": "Standard success/error response",
            }

    @staticmethod
    def _clean_code(raw: str) -> Optional[str]:
        """Extract Python code from LLM response (strip markdown fences)."""
        if not raw or not raw.strip():
            return None

        code = raw.strip()

        # Remove markdown code fences
        if code.startswith("```"):
            # Find the first newline after opening fence
            first_nl = code.find("\n")
            if first_nl != -1:
                code = code[first_nl:].strip()
            # Remove trailing fence
            if code.endswith("```"):
                code = code[:-3].strip()
            elif code.endswith("```"):
                code = code[:-3].strip()

        # Remove markdown fence variants
        for fence in ["```python", "```py", "```"]:
            if code.startswith(fence):
                code = code[len(fence):].strip()
            if code.endswith(fence):
                code = code[:-len(fence)].strip()

        # Must start with shebang or import to be valid
        if code.startswith("#!/usr/bin/env python3") or code.startswith("import ") or code.startswith("from "):
            return code

        # If code doesn't look like Python, return None
        if "def " not in code and "class " not in code:
            logger.warning("Generated code doesn't look like Python: %.100s", code)
            return None

        return code
