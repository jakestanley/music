import os
from typing import Dict, Optional

import requests


class UpSnapClient:
    def __init__(self, host: str, username: str, password: str) -> None:
        self.host = host.rstrip("/")
        self.username = username
        self.password = password

    def authenticate(self) -> str:
        url = f"{self.host}/api/collections/_superusers/auth-with-password"
        payload = {"identity": self.username, "password": self.password}
        resp = requests.post(url, json=payload, timeout=30)
        if not resp.ok:
            raise SystemExit(f"Request failed (HTTP {resp.status_code}): POST {url}\n{resp.text[:1000]}")
        data = resp.json()
        token = data.get("token")
        if not token:
            raise SystemExit("Failed to authenticate with UpSnap")
        return str(token)

    def _auth_headers(self, token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def list_devices(self, token: str) -> Dict:
        url = f"{self.host}/api/collections/devices/records"
        resp = requests.get(url, headers=self._auth_headers(token), timeout=30)
        if not resp.ok:
            raise SystemExit(f"Request failed (HTTP {resp.status_code}): GET {url}\n{resp.text[:1000]}")
        return resp.json()

    def find_device_id_by_name(self, token: str, name: str) -> Optional[str]:
        data = self.list_devices(token)
        for record in data.get("items", []):
            if record.get("name") == name:
                return record.get("id")
        return None

    def wake(self, token: str, device_id: str) -> None:
        url = f"{self.host}/api/upsnap/wake/{device_id}"
        resp = requests.get(url, headers=self._auth_headers(token), timeout=30)
        if not resp.ok:
            raise SystemExit(f"Request failed (HTTP {resp.status_code}): GET {url}\n{resp.text[:1000]}")

    def shutdown(self, token: str, device_id: str) -> None:
        url = f"{self.host}/api/upsnap/shutdown/{device_id}"
        resp = requests.get(url, headers=self._auth_headers(token), timeout=30)
        if not resp.ok:
            raise SystemExit(f"Request failed (HTTP {resp.status_code}): GET {url}\n{resp.text[:1000]}")


def load_upsnap_client() -> "UpSnapClient":
    host = os.environ.get("UPSNAP_HOST", "")
    username = os.environ.get("UPSNAP_USERNAME", "")
    password = os.environ.get("UPSNAP_PASSWORD", "")
    if not host or not username or not password:
        raise SystemExit("Missing required env var: UPSNAP_HOST/UPSNAP_USERNAME/UPSNAP_PASSWORD")
    return UpSnapClient(host, username, password)
