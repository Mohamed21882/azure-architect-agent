"""Reset a TE-1 portal user's password.

Usage: venv/bin/python scripts/reset_password.py <username>

The new password is read twice with getpass, so it is never echoed or stored in
shell history. It is hashed with brain.db.database.hash_password (the same bcrypt
hash used at registration). All of the user's existing sessions are revoked.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.db.database import init_db, reset_password, user_exists  # noqa: E402

_MIN_LENGTH = 8


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset a TE-1 portal user's password.")
    parser.add_argument("username", help="exact username (case-sensitive)")
    username = parser.parse_args().username

    init_db()
    if not user_exists(username):
        print(f"Error: no user named '{username}' exists. Nothing was changed.", file=sys.stderr)
        return 1

    try:
        password = getpass.getpass(f"New password for '{username}': ")
        confirm = getpass.getpass("Confirm new password: ")
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Nothing was changed.", file=sys.stderr)
        return 130

    if password != confirm:
        print("Error: passwords do not match. Nothing was changed.", file=sys.stderr)
        return 1
    if len(password) < _MIN_LENGTH:
        print(f"Error: password must be at least {_MIN_LENGTH} characters. "
              "Nothing was changed.", file=sys.stderr)
        return 1

    if not reset_password(username, password):
        print(f"Error: no user named '{username}' exists. Nothing was changed.", file=sys.stderr)
        return 1

    print(f"Password for '{username}' updated. Existing sessions were signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
