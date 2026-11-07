"""Checks DATABASE_URL_SESSION from .env without ever printing any part of it.

Usage: uv run python scripts/check-db-url.py
"""

import re
import sys
from pathlib import Path

import psycopg


def read_env_value(name: str) -> str:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^\s*{name}\s*=(.*)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def main() -> int:
    url = read_env_value("DATABASE_URL_SESSION")
    problems: list[str] = []
    if not url:
        print("DATABASE_URL_SESSION is empty. Paste the Session pooler string and save .env.")
        return 1
    if not url.startswith(("postgresql://", "postgres://")):
        problems.append("does not start with postgresql://")
    if "[" in url or "]" in url:
        problems.append("square brackets present: delete the [ ] around the password")
    if "YOUR-PASSWORD" in url:
        problems.append("placeholder [YOUR-PASSWORD] not replaced")
    if url.count("@") != 1:
        problems.append("password must not contain @ (reset it to letters and numbers only)")
    if not re.search(r":5432/", url):
        problems.append("port is not 5432: use the Session pooler string, not Transaction")
    if problems:
        print("Not connecting yet. Fix in .env:")
        for p in problems:
            print("  -", p)
        return 1
    try:
        with psycopg.connect(url, connect_timeout=10, prepare_threshold=None) as conn:
            version = conn.execute("show server_version").fetchone()
            tables = conn.execute(
                "select count(*) from information_schema.tables where table_schema = 'public'"
            ).fetchone()
    except psycopg.OperationalError as exc:
        text = str(exc).lower()
        if "password authentication failed" in text:
            reason = "wrong password"
        elif "tenant or user not found" in text:
            reason = "wrong user/host: copy the string again from Connect → Session pooler"
        elif "timeout" in text or "could not translate host" in text:
            reason = "host unreachable: check the host part and your internet connection"
        else:
            reason = "connection refused by the server"
        print("Not connected:", reason)
        return 1
    print("Connected. Postgres", version[0] if version else "?")
    print("Public tables:", tables[0] if tables else "?", "(0 means migrations have not run yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
