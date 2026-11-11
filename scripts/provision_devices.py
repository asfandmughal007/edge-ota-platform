"""Bootstrap a fleet: publish software versions and register devices.

Run by the ``provision`` compose service. Uses only the standard library so
it works in any container.

Each device's token is written to ``<tokens-dir>/<device_id>/device_token``,
readable only by the agent's user. The backend reveals a token exactly once, so
re-running must be careful: a token file that still authenticates is left
alone (rotating it would lock out the running agent), and only a missing or
rejected one is replaced by rotating the device's token.

Environment:
    DEVICE_MGMT_URL            backend base URL (default http://backend:8000)
    DEVICE_MGMT_ADMIN_API_KEY  operator key (required)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

AGENT_UID = 10001


def call(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base_url + path,
        data=data,
        method=method,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def wait_for_backend(base_url: str, attempts: int = 30) -> None:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(base_url + "/readyz", timeout=3):
                return
        except OSError:
            time.sleep(1)
    sys.exit(f"backend at {base_url} did not become ready")


def read_token(tokens_dir: Path, device_id: str) -> str | None:
    try:
        text = (tokens_dir / device_id / "device_token").read_text(encoding="utf-8")
    except OSError:
        return None
    return text.strip() or None


def token_works(base_url: str, device_id: str, token: str) -> bool:
    """True if the backend accepts this token for this device."""
    request = urllib.request.Request(
        f"{base_url}/devices/{device_id}/deployments/next",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False
        raise


def write_token(tokens_dir: Path, device_id: str, token: str) -> None:
    """Replace the token file atomically, so a failure never leaves a half-written one."""
    directory = tokens_dir / device_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "device_token"
    temporary = directory / ".device_token.tmp"
    temporary.write_text(token + "\n", encoding="utf-8")
    temporary.chmod(0o400)
    try:
        os.chown(temporary, AGENT_UID, AGENT_UID)
    except (PermissionError, AttributeError, OSError):
        # Not running as root (or not on Linux): leave ownership alone.
        temporary.chmod(0o444)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--devices", nargs="*", default=[], help="device IDs to register"
    )
    parser.add_argument("--versions", nargs="*", default=[], help="versions to publish")
    parser.add_argument("--image-prefix", default="localhost:5000/my-edge-app")
    parser.add_argument("--tokens-dir", type=Path, default=Path("/tokens"))
    args = parser.parse_args()

    base_url = os.environ.get("DEVICE_MGMT_URL", "http://backend:8000").rstrip("/")
    api_key = os.environ.get("DEVICE_MGMT_ADMIN_API_KEY")
    if not api_key:
        sys.exit("DEVICE_MGMT_ADMIN_API_KEY is not set")

    wait_for_backend(base_url)

    for version in args.versions:
        status, body = call(
            base_url,
            api_key,
            "POST",
            "/versions",
            {"version": version, "image": f"{args.image_prefix}:{version}"},
        )
        if status not in (201, 409):
            sys.exit(f"publishing {version} failed: HTTP {status} {body}")
        print(
            f"version {version}: {'published' if status == 201 else 'already exists'}"
        )

    for device_id in args.devices:
        status, body = call(
            base_url, api_key, "POST", "/devices", {"device_id": device_id}
        )
        if status == 409:
            existing = read_token(args.tokens_dir, device_id)
            if existing is not None and token_works(base_url, device_id, existing):
                print(f"device {device_id}: already provisioned, token still valid")
                continue
            status, body = call(
                base_url, api_key, "POST", f"/devices/{device_id}/token"
            )
            action = "token rotated (missing or rejected)"
        else:
            action = "registered"
        if status not in (200, 201):
            sys.exit(f"provisioning {device_id} failed: HTTP {status} {body}")
        write_token(args.tokens_dir, device_id, body["device_token"])
        print(
            f"device {device_id}: {action}, token written"
        )  # the token itself is never printed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
