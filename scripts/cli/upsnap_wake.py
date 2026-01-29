import argparse
import os
from pathlib import Path

from scripts.core.env import load_env, require_var
from scripts.core.logging import log
from scripts.upsnap.client import load_upsnap_client


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--print-token", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args, extra = parser.parse_known_args()
    if extra:
        log(f"Usage: {Path(__file__).name} [--print-token] [--quiet]", quiet=False)
        return 1

    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / ".env"
    load_env(str(env_path))

    require_var("UPSNAP_HOST")
    require_var("UPSNAP_USERNAME")
    require_var("UPSNAP_PASSWORD")

    client = load_upsnap_client()
    token = client.authenticate()
    device_id = os.environ.get("UPSNAP_DEVICE_ID", "")
    if not device_id:
        require_var("UPSNAP_DEVICE_NAME")
        device_id = client.find_device_id_by_name(token, os.environ["UPSNAP_DEVICE_NAME"]) or ""

    if not device_id:
        log("Unable to resolve UpSnap device id", quiet=False)
        devices = client.list_devices(token)
        for record in devices.get("items", []):
            log(f"{record.get('id','')}  {record.get('name','')}", quiet=False)
        return 1

    if not args.quiet:
        log(f"Requesting UpSnap wake for device: {device_id}", quiet=False)

    client.wake(token, device_id)

    if args.print_token:
        print(f"{device_id}\t{token}")
    else:
        print(device_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
