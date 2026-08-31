"""Set the app password.

Run this yourself. The password is read from a hidden prompt, hashed in memory,
and the hash is written straight into backend/.env — it is never printed, so
there is nothing to copy and nothing to paste anywhere it should not go.

    ./.venv/bin/python scripts/set_password.py

Then restart the API. Changing the password ends every existing session.

Use --print-only if you would rather place the line yourself, but be aware the
hash is sensitive in its own right: this app derives the session signing key
from it, so anyone holding it can mint a valid session. Treat it like the
password, not like a checksum.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import hash_password  # noqa: E402

MIN_LENGTH = 12
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
KEY = "AUTH_PASSWORD_HASH"


def read_password() -> str:
    password = getpass.getpass("New password: ")
    if len(password) < MIN_LENGTH:
        print(f"Too short — use at least {MIN_LENGTH} characters.", file=sys.stderr)
        raise SystemExit(1)
    if password != getpass.getpass("Repeat: "):
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    return password


def write_env(encoded: str) -> None:
    """Replace the hash line in .env, leaving every other setting alone."""
    existing = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    kept = [line for line in existing if not line.startswith(f"{KEY}=")]
    kept.append(f"{KEY}={encoded}")
    ENV_PATH.write_text("\n".join(kept) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="print the hash instead of writing it to .env",
    )
    args = parser.parse_args()

    encoded = hash_password(read_password())

    if args.print_only:
        print(f"\n{KEY}={encoded}\n")
        print("Keep this secret — it can mint a session, not just verify one.")
        return

    write_env(encoded)
    print(f"\nWritten to {ENV_PATH} (gitignored). The hash was not printed.")
    print("Restart the API. Existing sessions are now invalid.")


if __name__ == "__main__":
    main()
