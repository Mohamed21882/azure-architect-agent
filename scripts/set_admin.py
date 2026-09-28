"""Grant or revoke TE-1 portal admin rights.

Usage: venv/bin/python scripts/set_admin.py <username> [--revoke]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brain.db.database import init_db, set_admin  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant or revoke TE-1 admin rights.")
    parser.add_argument("username", help="exact username (case-sensitive)")
    parser.add_argument("--revoke", action="store_true", help="remove admin rights")
    args = parser.parse_args()

    init_db()  # applies the is_admin migration if needed
    if not set_admin(args.username, not args.revoke):
        print(f"Error: no user named '{args.username}' exists. Nothing was changed.", file=sys.stderr)
        return 1
    print(f"'{args.username}' is {'no longer' if args.revoke else 'now'} an admin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
