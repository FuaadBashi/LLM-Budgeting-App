"""Generate the password hash for AUTH_PASSWORD_HASH.

Run this yourself — the password is read from a hidden prompt, hashed in
memory, and never written anywhere by this script. Only the hash is printed.

    ./.venv/bin/python scripts/set_password.py

Then put the printed line in backend/.env, which is gitignored.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import hash_password  # noqa: E402

MIN_LENGTH = 12


def main() -> None:
    password = getpass.getpass("New password: ")
    if len(password) < MIN_LENGTH:
        print(f"Too short — use at least {MIN_LENGTH} characters.", file=sys.stderr)
        raise SystemExit(1)
    if password != getpass.getpass("Repeat: "):
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)

    print("\nAdd this line to backend/.env (it is gitignored):\n")
    print(f"AUTH_PASSWORD_HASH={hash_password(password)}")
    print("\nRestart the API. Existing sessions are invalidated by the change.")


if __name__ == "__main__":
    main()
