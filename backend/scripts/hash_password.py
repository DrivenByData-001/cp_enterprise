"""Generate an APP_AUTH_PASSWORD_HASH value for the single-user login
(app/auth.py, docs/20-render-deployment.md).

Usage:
    python scripts/hash_password.py

Prompts for the password twice (hidden input, not echoed to the terminal),
then prints a bcrypt hash to paste into Render's environment variables (or a
local `.env`) as APP_AUTH_PASSWORD_HASH. The plaintext password is never
written anywhere by this script.
"""

import getpass
import sys

import bcrypt


def main() -> int:
    password = getpass.getpass("New app password: ")
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        return 1
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        return 1

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    print("\nAPP_AUTH_PASSWORD_HASH=" + hashed)
    print("\nSet this as an environment variable (Render dashboard, or your local .env) —")
    print("never commit it to git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
