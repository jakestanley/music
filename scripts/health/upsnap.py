"""UpSnap health check — validates connectivity, auth, and device lookup.

Usage:
    python scripts/health/upsnap.py
"""

import os
import subprocess
import sys
from pathlib import Path

# --- Venv bootstrap: re-exec under the venv python if not already there ---
_ROOT = Path(__file__).parent.parent.parent
_VENV = _ROOT / ".venv"
_VENV_PYTHON = _VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

if not Path(sys.executable).is_relative_to(_VENV):
    if not _VENV_PYTHON.exists():
        print("Initialising venv...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(_VENV)], check=True)
        subprocess.run(
            [str(_VENV_PYTHON), "-m", "pip", "install", "-r", str(_ROOT / "requirements.txt")],
            check=True,
        )
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)
# --------------------------------------------------------------------------

sys.path.insert(0, str(_ROOT))

from scripts.core.env import load_env
load_env(str(_ROOT / ".env"))

from typing import Optional

import requests

from scripts.upsnap.batch import _load_config


def _ok(msg: str) -> None:
    print(f"  [ OK ] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def run() -> None:
    print("=== UpSnap Health Check ===\n")

    # --- Config ---
    print("--- Config ---")
    try:
        config = _load_config()
    except SystemExit as exc:
        _fail(f"Config error: {exc}")
        return

    _info(f"URL:         {config.base_url}")
    _info(f"TLS mode:    {config.tls_mode}")
    _info(f"Username:    {config.username}")
    _info(f"Device name: {config.device_name}")
    print()

    verify = config.verify
    devices_url = f"{config.base_url}/api/collections/devices/records"

    # --- Connectivity ---
    print("--- Connectivity ---")
    health_url = f"{config.base_url}/api/health"
    try:
        resp = requests.get(health_url, timeout=10, verify=verify)
        if resp.ok:
            _ok(f"Server reachable (HTTP {resp.status_code})")
        else:
            _warn(f"Server returned HTTP {resp.status_code}")
    except requests.exceptions.RequestException as exc:
        _fail(f"Cannot reach server: {exc}")
        return
    print()

    # --- Authentication ---
    print("--- Authentication ---")
    auth_url = f"{config.base_url}/api/collections/_superusers/auth-with-password"
    token: Optional[str] = None
    try:
        resp = requests.post(
            auth_url,
            json={"identity": config.username, "password": config.password},
            timeout=10,
            verify=verify,
        )
        if resp.ok:
            token = resp.json().get("token")
            if token:
                _ok(f"Authenticated as {config.username}")
            else:
                _fail("Auth response missing token field")
        else:
            _fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
    except requests.exceptions.RequestException as exc:
        _fail(f"Request failed: {exc}")
    if not token:
        print()
        print("=== Done ===")
        return
    print()

    # --- Device list ---
    print("--- Device list ---")
    auth_items: Optional[list] = None
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(devices_url, headers=headers, params={"perPage": "200"}, timeout=10, verify=verify)
        if resp.ok:
            payload = resp.json()
            auth_items = payload.get("items", [])
            total = payload.get("totalItems", len(auth_items))
            if auth_items:
                names = [item.get("name", "(unnamed)") for item in auth_items]
                _ok(f"{total} device(s) visible: {names}")
            else:
                _fail("0 devices returned")
                _info(f"Response body: {resp.text[:300]}")
        else:
            _fail(f"HTTP {resp.status_code}")
            _info(f"Response body: {resp.text[:300]}")
    except requests.exceptions.RequestException as exc:
        _fail(f"Request failed: {exc}")
    print()

    # --- Device name lookup ---
    print("--- Device name lookup ---")
    if not auth_items:
        _fail("Skipped — no devices visible")
    else:
        wanted = config.device_name.strip().lower()
        matches = [
            item
            for item in auth_items
            if isinstance(item.get("name"), str) and item["name"].strip().lower() == wanted
        ]
        if len(matches) == 1:
            device_id = matches[0].get("id", "(no id)")
            _ok(f"Found '{config.device_name}' (id={device_id})")
        elif not matches:
            visible = [item.get("name", "") for item in auth_items]
            _fail(f"No match for '{config.device_name}' — visible: {visible}")
        else:
            _warn(f"{len(matches)} devices match '{config.device_name}' (expected 1)")
    print()

    print("=== Done ===")


if __name__ == "__main__":
    run()
