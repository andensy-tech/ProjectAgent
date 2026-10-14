"""
Skill templates — boilerplate code patterns for generated skills.

MAIN_PY_TEMPLATE is the standard skeleton used by the Skill Generator
when creating new skills. It defines the stdin/stdout JSON protocol,
argument parsing, and standardized return format.
"""

MAIN_PY_TEMPLATE = '''#!/usr/bin/env python3
"""{description}"

import json
import sys
from typing import Dict, Any


def main(params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the skill with the given parameters."""
    try:
        # TODO: implement skill logic here
        # params contains the input parameters as a dict

        result = {
            "success": True,
            "message": "Execution completed",
            "data": params,
        }
        return result
    except Exception as e:
        return {"success": False, "message": str(e), "data": {}}


if __name__ == "__main__":
    raw = sys.stdin.read()
    params = json.loads(raw) if raw.strip() else {}
    result = main(params)
    print(json.dumps(result, ensure_ascii=False, indent=2))
'''

SKILL_GENERATION_SYSTEM_PROMPT = """You are a Python code generator for AI agent skills. You produce clean, production-ready, single-file Python scripts.

## Protocol
Each skill reads JSON parameters from stdin (first and only line of input), executes the requested logic, and writes a JSON result as the LAST line of stdout. All logging, debug, or progress output MUST go to stderr.

## Return format
All skills MUST return a dict with three keys:
- "success": bool — whether the operation succeeded
- "message": str — human-readable result message
- "data": dict — structured result data

## Coding rules
1. Use type annotations for all function signatures
2. Handle all exceptions with try/except, never let the script crash
3. Use the `if __name__ == "__main__":` guard
4. Include a module-level docstring describing the skill's purpose
5. Use f-strings, not %-formatting or .format()
6. Prefer standard library; use `requests` for HTTP only when needed
7. Keep the skill focused on ONE task — single responsibility principle

## Available imports
Standard: json, sys, os, re, typing, datetime, subprocess, logging, pathlib, collections, math, random
Third-party: requests (pre-installed in the environment)

Generate ONLY the Python code. No markdown fences, no explanation. Start with #!/usr/bin/env python3."""

__all__ = ["MAIN_PY_TEMPLATE", "SKILL_GENERATION_SYSTEM_PROMPT"]
