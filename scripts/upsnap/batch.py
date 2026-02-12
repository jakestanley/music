import atexit
import base64
import json
import ipaddress
import os
import tempfile
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from scripts.core.env import require_var
from scripts.core.logging import log

_wake_done = False
_wake_success = False
_waker: Optional["UpSnapBatchWaker"] = None


@dataclass(frozen=True)
class UpSnapConfig:
    base_url: str
    token: str
    device_name: str
    verify: str | bool
    tls_mode: str
    poll_seconds: float
    timeout_seconds: float
    request_timeout_seconds: float


def _read_bool_env(name: str) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"Invalid {name}: expected number")


def _validate_dns_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit("Invalid UPSNAP_URL: expected http(s) URL with a hostname")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return raw_url.rstrip("/")
    raise SystemExit("Invalid UPSNAP_URL: raw IP addresses are not allowed")


def _download_ca_cert(url: str) -> str:
    resp = requests.get(url, timeout=30)
    if not resp.ok:
        raise SystemExit(f"Failed to download UPSNAP_CA_CERT: HTTP {resp.status_code}")
    handle = tempfile.NamedTemporaryFile(prefix="upsnap-ca-", suffix=".crt", delete=False)
    handle.write(resp.content)
    handle.flush()
    handle.close()
    atexit.register(lambda: os.path.exists(handle.name) and os.remove(handle.name))
    return handle.name


def _load_config() -> UpSnapConfig:
    base_url = _validate_dns_url(require_var("UPSNAP_URL"))
    token = require_var("UPSNAP_BEARER_TOKEN")
    device_name = require_var("UPSNAP_DEVICE_NAME")
    ca_cert = os.environ.get("UPSNAP_CA_CERT")
    insecure_tls = _read_bool_env("UPSNAP_INSECURE_TLS")

    verify: str | bool = True
    tls_mode = "system trust"
    if ca_cert:
        if ca_cert.startswith("http://") or ca_cert.startswith("https://"):
            verify = _download_ca_cert(ca_cert)
            tls_mode = "custom CA (downloaded)"
        else:
            verify = ca_cert
            tls_mode = "custom CA"
    elif insecure_tls:
        verify = False
        tls_mode = "insecure (UPSNAP_INSECURE_TLS)"

    poll_seconds = _read_float_env("UPSNAP_STATUS_POLL_SECS", 5.0)
    timeout_seconds = _read_float_env("UPSNAP_STATUS_TIMEOUT_SECS", 120.0)
    request_timeout_seconds = _read_float_env("UPSNAP_REQUEST_TIMEOUT_SECS", 10.0)
    return UpSnapConfig(
        base_url=base_url,
        token=token,
        device_name=device_name,
        verify=verify,
        tls_mode=tls_mode,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        request_timeout_seconds=request_timeout_seconds,
    )


def _debug_enabled() -> bool:
    return _read_bool_env("UPSNAP_DEBUG")


def _summarize_device_names(items: list[dict]) -> str:
    names: list[str] = []
    for item in items:
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    if not names:
        return "[]"
    names = sorted(set(names))
    if len(names) > 20:
        return f"{names[:20]} (+{len(names) - 20} more)"
    return str(names)


def _jwt_expiry_from_env() -> Optional[datetime]:
    token = os.environ.get("UPSNAP_BEARER_TOKEN", "").strip()
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    pad_len = (-len(payload)) % 4
    payload += "=" * pad_len
    try:
        raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    exp = data.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _log_token_expiry_hint() -> None:
    expiry = _jwt_expiry_from_env()
    if not expiry:
        log("UpSnap token may have expired; rotate UPSNAP_BEARER_TOKEN.", quiet=False)
        return
    now = datetime.now(timezone.utc)
    status = "expired" if expiry <= now else "expires"
    exp_str = expiry.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    log(f"UpSnap token {status} at {exp_str}; rotate UPSNAP_BEARER_TOKEN if needed.", quiet=False)


def validate_upsnap_env() -> None:
    _load_config()


class UpSnapBatchWaker:
    def __init__(self, config: UpSnapConfig) -> None:
        self.config = config
        self._device_id: Optional[str] = None

    @classmethod
    def from_env(cls) -> "UpSnapBatchWaker":
        config = _load_config()
        log(f"UpSnap TLS mode: {config.tls_mode}", quiet=False)
        if config.tls_mode.startswith("insecure"):
            log("Warning: UPSNAP_INSECURE_TLS enabled; TLS verification is disabled.", quiet=False)
        return cls(config)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.config.token}"}

    def _get(self, url: str, operation: str, *, params: Optional[Dict[str, str]] = None) -> requests.Response:
        try:
            return requests.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.config.request_timeout_seconds,
                verify=self.config.verify,
            )
        except requests.exceptions.ReadTimeout:
            raise SystemExit(
                f"UpSnap {operation} timed out after {self.config.request_timeout_seconds:.1f}s "
                f"(GET {url}). The target may still be asleep or unreachable."
            )
        except requests.exceptions.RequestException as exc:
            raise SystemExit(f"UpSnap {operation} request failed: {exc}")

    def resolve_device_id(self) -> str:
        if self._device_id:
            return self._device_id
        log(f"Resolving UpSnap device name: {self.config.device_name}", quiet=False)
        url = f"{self.config.base_url}/api/collections/devices/records"
        params = {"filter": f'name="{self.config.device_name}"'}
        if _debug_enabled():
            log(f"UpSnap debug: GET {url} params={params}", quiet=False)
        resp = self._get(url, "device lookup", params=params)
        if _debug_enabled():
            log(f"UpSnap debug: status={resp.status_code}", quiet=False)
        if not resp.ok:
            raise SystemExit(f"UpSnap device lookup failed: HTTP {resp.status_code}")
        payload = resp.json()
        items = payload.get("items", [])
        if len(items) != 1:
            if len(items) == 0:
                _log_token_expiry_hint()
            wanted = self.config.device_name.strip().lower()
            matches = [
                item
                for item in items
                if isinstance(item.get("name"), str) and item["name"].strip().lower() == wanted
            ]
            if len(matches) != 1:
                # Fallback: fetch all devices and perform a case-insensitive match.
                log("Exact name lookup failed; retrying with case-insensitive scan.", quiet=False)
                scan_params = {"perPage": "200"}
                if _debug_enabled():
                    log(f"UpSnap debug: GET {url} params={scan_params}", quiet=False)
                resp = self._get(url, "device lookup (scan)", params=scan_params)
                if _debug_enabled():
                    log(f"UpSnap debug: status={resp.status_code}", quiet=False)
                if not resp.ok:
                    raise SystemExit(f"UpSnap device lookup failed: HTTP {resp.status_code}")
                payload = resp.json()
                items = payload.get("items", [])
                if _debug_enabled():
                    log(
                        f"UpSnap debug: devices={len(items)} names={_summarize_device_names(items)}",
                        quiet=False,
                    )
                matches = [
                    item
                    for item in items
                    if isinstance(item.get("name"), str) and item["name"].strip().lower() == wanted
                ]
                if len(matches) != 1:
                    if len(items) == 0:
                        _log_token_expiry_hint()
                    raise SystemExit(
                        f"UpSnap device lookup expected 1 match for {self.config.device_name}; got {len(matches)}"
                    )
            items = matches
        device_id = items[0].get("id")
        if not device_id:
            raise SystemExit("UpSnap device lookup missing device id")
        self._device_id = str(device_id)
        return self._device_id

    def wake(self) -> None:
        device_id = self.resolve_device_id()
        url = f"{self.config.base_url}/api/upsnap/wake/{device_id}"
        log(f"UpSnap wake request issued: GET {url}", quiet=False)
        resp = self._get(url, "wake")
        if not resp.ok:
            log("UpSnap wake failed", quiet=False)
            raise SystemExit(f"UpSnap wake failed: HTTP {resp.status_code}")
        log("UpSnap wake request accepted", quiet=False)

    def status(self) -> str:
        device_id = self.resolve_device_id()
        url = f"{self.config.base_url}/api/collections/devices/records/{device_id}"
        log(f"UpSnap status request: GET {url}", quiet=False)
        resp = self._get(url, "status")
        if not resp.ok:
            raise SystemExit(f"UpSnap status failed: HTTP {resp.status_code}")
        try:
            payload: Dict[str, Any] = resp.json()
        except ValueError:
            body = (resp.text or "").strip()
            if len(body) > 200:
                body = body[:200] + "..."
            raise SystemExit(f"UpSnap status returned invalid JSON: HTTP {resp.status_code} body={body!r}")
        status = payload.get("status", "")
        if status is None:
            status = ""
        return str(status)

    def wait_until_ready(self) -> None:
        log("UpSnap status polling start", quiet=False)
        start = time.monotonic()
        while True:
            status = self.status()
            if status == "online":
                log("UpSnap status polling done: online", quiet=False)
                return
            if self.config.timeout_seconds and (time.monotonic() - start) > self.config.timeout_seconds:
                log("UpSnap status polling timed out", quiet=False)
                raise SystemExit("UpSnap wake timeout waiting for online status")
            time.sleep(self.config.poll_seconds)

    def require_ready(self) -> None:
        status = self.status()
        if status != "online":
            raise SystemExit(f"UpSnap status not online (status={status}); aborting batch submission")

    def sleep(self) -> None:
        device_id = self.resolve_device_id()
        url = f"{self.config.base_url}/api/upsnap/sleep/{device_id}"
        log(f"UpSnap sleep request issued: GET {url}", quiet=False)
        resp = self._get(url, "sleep")
        if not resp.ok:
            raise SystemExit(f"UpSnap sleep failed: HTTP {resp.status_code}")


def _get_waker() -> UpSnapBatchWaker:
    global _waker
    if _waker is None:
        _waker = UpSnapBatchWaker.from_env()
    return _waker


def ensure_awake() -> None:
    global _wake_done, _wake_success
    waker = _get_waker()
    if _wake_done:
        return
    status = waker.status()
    if status == "online":
        log("UpSnap status already online; skipping wake request", quiet=False)
        _wake_done = True
        return
    waker.wake()
    waker.wait_until_ready()
    _wake_done = True
    _wake_success = True


def require_ready() -> None:
    waker = _get_waker()
    waker.require_ready()


def sleep_if_awake() -> None:
    global _wake_success
    if not _wake_success:
        return
    waker = _get_waker()
    waker.sleep()
