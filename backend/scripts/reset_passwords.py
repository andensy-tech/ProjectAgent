"""Reset a user's password by username (direct DB write).

Usage:
    cd backend
    python scripts/reset_passwords.py <username> [new_password]

If new_password is omitted, the username is used as the initial password
and force_password_change is set so the user must change it on next login.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

from app.core.security import get_password_hash


async def main() -> None:
    """Update the given user's password in the local SQLite database."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/reset_passwords.py <username> [new_password]")
        sys.exit(1)

    username = sys.argv[1]
    new_password = sys.argv[2] if len(sys.argv) > 2 else username
    force_change = 0 if len(sys.argv) > 2 else 1

    async with aiosqlite.connect("projectagent.db") as db:
        hashed = get_password_hash(new_password)
        cursor = await db.execute(
            "UPDATE users SET hashed_password=?, force_password_change=? WHERE username=?",
            (hashed, force_change, username),
        )
        await db.commit()
        if cursor.rowcount == 0:
            print(f"User not found: {username}")
            sys.exit(1)
        print(f"OK: {username} password reset (force_password_change={force_change})")


if __name__ == "__main__":
    asyncio.run(main())
