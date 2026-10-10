#!/usr/bin/env python3
"""Create the sandbox admin (if missing) and an API key, then save the key to .env.

Endpoints verified against the Immich v3.3.1 OpenAPI spec:
  GET  /api/server/config       -> isInitialized
  POST /api/auth/admin-sign-up  -> {email, name, password}
  POST /api/auth/login          -> {email, password} -> accessToken
  POST /api/api-keys            -> {name, permissions} -> secret (shown once)
  GET  /api/users/me            -> verify a key works

Credentials come from IMMICH_ADMIN_EMAIL / IMMICH_ADMIN_PASSWORD or a prompt.
They are never written to disk or printed; the API key is never printed.

Usage: python scripts/bootstrap.py [--url http://127.0.0.1:2283] [--env-file .env]
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
KEY_NAME = "shutterstorm"
TIMEOUT = 15.0


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def read_env_value(env_file: Path, name: str) -> str:
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        m = re.match(rf"^{name}=(.*)$", line)
        if m:
            return m.group(1).strip().strip("\"'")
    return ""


def write_env_value(env_file: Path, name: str, value: str) -> None:
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    out, done = [], False
    for line in lines:
        if re.match(rf"^{name}=", line):
            out.append(f"{name}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{name}={value}")
    env_file.write_text("\n".join(out) + "\n")
    env_file.chmod(0o600)


def key_works(client: httpx.Client, key: str) -> bool:
    r = client.get("/api/users/me", headers={"x-api-key": key})
    return r.status_code == 200


def ask_credentials(need_name: bool) -> tuple[str, str, str]:
    email = os.environ.get("IMMICH_ADMIN_EMAIL") or input("Admin email: ").strip()
    password = os.environ.get("IMMICH_ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    name = os.environ.get("IMMICH_ADMIN_NAME", "admin")
    if need_name and "IMMICH_ADMIN_NAME" not in os.environ:
        name = input("Admin display name [admin]: ").strip() or "admin"
    if not email or not password:
        die("email and password are required")
    return email, password, name


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("IMMICH_URL", "http://127.0.0.1:2283"))
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = ap.parse_args()

    # Sandbox only: refuse anything that is not this machine.
    host = httpx.URL(args.url).host
    if host not in ("127.0.0.1", "localhost", "::1"):
        die(f"refusing non-local target {host!r}; ShutterStorm only targets the local sandbox")

    with httpx.Client(base_url=args.url, timeout=TIMEOUT) as client:
        # 1. Is Immich up?
        try:
            client.get("/api/server/ping").raise_for_status()
        except httpx.HTTPError as e:
            die(f"Immich is not reachable at {args.url} ({e}). Run: make up && make health")

        # 2. Already bootstrapped? Re-running must be a no-op.
        existing = read_env_value(args.env_file, "IMMICH_API_KEY")
        if existing and key_works(client, existing):
            print("OK: IMMICH_API_KEY in .env is already valid. Nothing to do.")
            return

        # 3. Make sure an admin exists.
        initialized = client.get("/api/server/config").json().get("isInitialized", False)
        email, password, name = ask_credentials(need_name=not initialized)
        if not initialized:
            r = client.post(
                "/api/auth/admin-sign-up",
                json={"email": email, "name": name, "password": password},
            )
            if r.status_code >= 400:
                die(f"admin sign-up failed: HTTP {r.status_code}")
            print("Created admin account.")

        # 4. Log in.
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code >= 400:
            die(
                f"login failed: HTTP {r.status_code}. Check the email/password, or reset with:\n"
                "  docker exec -it immich_server immich-admin reset-admin-password"
            )
        token = r.json()["accessToken"]

        # 5. Create the API key (secret is returned only once).
        r = client.post(
            "/api/api-keys",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": KEY_NAME, "permissions": ["all"]},
        )
        if r.status_code >= 400:
            die(f"API key creation failed: HTTP {r.status_code}")
        secret = r.json()["secret"]

        # 6. Save it, then prove it works.
        write_env_value(args.env_file, "IMMICH_API_KEY", secret)
        if not key_works(client, secret):
            die("key was created but a test request with it failed")
        print(f"OK: API key saved to {args.env_file.name} and verified.")


if __name__ == "__main__":
    main()
