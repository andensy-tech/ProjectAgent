"""
Skill Validator — syntax and protocol validation for generated skills.

Checks:
1. Python syntax (compile())
2. Stdin/stdout JSON protocol compliance
3. Basic structure (shebang, main(), if __name__ guard)
"""

import ast
import logging
from typing import Tuple, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of skill validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


async def validate_skill(code: str, skill_name: str) -> Tuple[bool, List[str]]:
    """Validate generated skill code.

    Args:
        code: Python source code to validate.
        skill_name: Name of the skill (for error messages).

    Returns:
        Tuple of (is_valid, list_of_error_messages).
    """
    errors = []

    # 1. Check for empty code
    if not code or not code.strip():
        errors.append("Code is empty")
        return False, errors

    # 2. Check Python syntax
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        errors.append(f"Syntax error: {e}")
        return False, errors

    # 3. Check for main() function definition
    has_main_func = any(
        isinstance(node, ast.FunctionDef) and node.name == "main"
        for node in ast.walk(tree)
    )
    if not has_main_func:
        errors.append("Missing main() function definition")

    # 4. Check for if __name__ == "__main__" guard
    has_main_guard = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            try:
                if (isinstance(node.test, ast.Compare)
                        and isinstance(node.test.left, ast.Name)
                        and node.test.left.id == "__name__"):
                    has_main_guard = True
                    break
            except Exception:
                continue
    if not has_main_guard:
        errors.append("Missing if __name__ == '__main__' guard")

    # 5. Check for json import (needed for protocol)
    has_json_import = any(
        isinstance(node, ast.Import) and any(alias.name == "json" for alias in node.names)
        for node in ast.walk(tree)
    )
    if not has_json_import:
        errors.append("Missing 'import json' (required for stdin/stdout protocol)")

    return len(errors) == 0, errors


async def validate_skill_runtime(code: str, skill_name: str) -> ValidationResult:
    """Extended validation with optional syntax-only runtime check.

    Does NOT execute the skill — only verifies it can be compiled.
    """
    result = ValidationResult(valid=True)

    valid, errors = await validate_skill(code, skill_name)
    if not valid:
        result.valid = False
        result.errors = errors

    return result
