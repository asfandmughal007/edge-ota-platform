"""Walk the local stack through the full OTA story.

    docker compose up -d --build
    python scripts/demo.py

1. install 1.2.0 on all three devices
2. upgrade all three to 1.3.0
3. push the deliberately broken 1.4.0 to device-003 and watch it roll back

Standard library only. Reads DEVICE_MGMT_ADMIN_API_KEY from the environment or ./.env.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("DEVICE_MGMT_URL", "http://127.0.0.1:8000")
DEVICES = ["device-001", "device-002", "device-003"]
TERMINAL = {"succeeded", "failed", "rolled_back"}


def load_api_key() -> str:
    key = os.environ.get("DEVICE_MGMT_ADMIN_API_KEY")
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEVICE_MGMT_ADMIN_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("DEVICE_MGMT_ADMIN_API_KEY is not set (environment or .env)")
    return key


API_KEY = load_api_key()


def api(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode()}")


def wait_until(description: str, predicate: Any, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(1)
    sys.exit(f"timed out waiting for: {description}")


def deploy(device_ids: list[str], version: str) -> list[dict[str, Any]]:
    print(f"\n=== deploying {version} to {', '.join(device_ids)}")
    created = {
        device_id: api("POST", "/deployments", {"device_id": device_id, "target_version": version})
        for device_id in device_ids
    }
    results: dict[str, dict[str, Any]] = {}

    def all_finished() -> bool:
        for device_id, deployment in created.items():
            current = api("GET", f"/deployments/{deployment['id']}")
            if current["status"] in TERMINAL:
                results[device_id] = current
        return len(results) == len(created)

    wait_until(f"deployments of {version}", all_finished)
    for device_id, result in sorted(results.items()):
        steps = " -> ".join(event["status"] for event in result["events"])
        print(f"{device_id}: {steps}")
        if result["message"]:
            print(f"    {result['message'][:200]}")
    return list(results.values())


def main() -> int:
    print("waiting for all devices to report in...")
    wait_until(
        "all devices online",
        lambda: all(api("GET", f"/devices/{d}")["status"] == "online" for d in DEVICES),
    )

    deploy(DEVICES, "1.2.0")
    deploy(DEVICES, "1.3.0")
    deploy(["device-003"], "1.4.0")

    time.sleep(6)  # let a heartbeat report the post-rollback state

    print("\n=== device state")
    for device_id in DEVICES:
        health = api("GET", f"/devices/{device_id}/health")
        app = health["application"] or {}
        print(
            f"{device_id}: {health['status']}, running {health['current_version']}, "
            f"app healthy={app.get('healthy')}"
        )

    print("\n=== deployment history of device-003")
    for deployment in reversed(api("GET", "/devices/device-003/deployments")):
        print(f"{deployment['target_version']:>6}  {deployment['status']}")

    print("\n=== fleet summary")
    summary = api("GET", "/fleet/summary")
    print(json.dumps({k: summary[k] for k in summary if k != "devices_by_status"}, indent=2))
    print("devices_by_status:", summary["devices_by_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
