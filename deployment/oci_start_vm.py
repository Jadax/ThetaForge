#!/usr/bin/env python3
"""Persistent OCI free-tier instance starter.

Oracle free-tier Micro/ARM capacity is oversubscribed: a stopped instance
often returns "Out of host capacity" (or 429 TooManyRequests during a burst)
when asked to START. This script retries START until the instance reaches
RUNNING, then reports its public IP, so the always-on Bridge VM can come back
without a human babysitting the console.

Usage:
  python scripts/oci_start_vm.py [--instance-id OCID] [--region REGION]
  # defaults: locate the instance by display name in the region/AD the
  # launch/retry helpers used, notify over ntfy.sh when it boots. See
  # deployment/arm_retry_vm.py for the launch-side counterpart.
"""
import json
import os
import subprocess
import sys
import time

REGION = os.environ.get("OCI_REGION", "af-johannesburg-1")
DISPLAY_NAME = "thetaforge-bridge-vm"
NTFY_URL = "https://ntfy.sh/thetaforge-arm-ozc7ebhka1345mgu6v8nwdqp"
LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "oci_start_vm.log"
)
RETRY_BASE = 30      # seconds between attempts (backed off on 429/capacity)
RETRY_MAX = 300
RUNNING_TIMEOUT = 900

os.environ.setdefault("OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING", "True")


def run_cli(*args):
    return subprocess.run(
        ["oci", *args, "--region", REGION], capture_output=True, text=True
    )


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def notify(title, message):
    try:
        subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-H", f"Title: {title}",
             "-d", message, NTFY_URL],
            timeout=20,
        )
    except Exception as e:
        log(f"NOTIFY failed: {e}")


def find_instance_id():
    r = run_cli("compute", "instance", "list",
                "--compartment-id", _compartment(),
                "--availability-domain", _ad(),
                "--display-name", DISPLAY_NAME)
    data = json.loads(r.stdout or "{}").get("data", [])
    if not data:
        raise SystemExit(f"no instance named {DISPLAY_NAME} found in {REGION}")
    row = data[0]
    return row["id"], row.get("lifecycle-state") or row.get("lifecycle_state")


def _compartment():
    # tenancy ocid doubles as the root compartment; matches the launch helpers
    return "ocid1.tenancy.oc1..aaaaaaaa5ptogdhsltwkrqf6sp4ygzkmutu6le4pt2eyccndqzevt5obroca"


def _ad():
    return "vGbj:AF-JOHANNESBURG-1-AD-1"


def get_state(instance_id):
    r = run_cli("compute", "instance", "get", "--instance-id", instance_id)
    row = json.loads(r.stdout or "{}").get("data", {})
    return (row.get("lifecycle-state") or row.get("lifecycle_state")), r.stdout


def public_ip(instance_id):
    r = run_cli("compute", "instance", "list-vnics", "--instance-id", instance_id,
                "--compartment-id", _compartment())
    for vnic in json.loads(r.stdout or "{}").get("data", []):
        if vnic.get("public-ip"):
            return vnic["public-ip"]
    return None


def main():
    instance_id = None
    for arg in sys.argv[1:]:
        if arg.startswith("--instance-id="):
            instance_id = arg.split("=", 1)[1]

    if not instance_id:
        instance_id, state = find_instance_id()
        log(f"instance {DISPLAY_NAME} = {instance_id} (state={state})")

    backoff = RETRY_BASE
    attempt = 0
    while True:
        attempt += 1
        state, _ = get_state(instance_id)
        if state == "RUNNING":
            ip = public_ip(instance_id)
            log(f"RUNNING! public ip={ip} after {attempt} attempts")
            notify(f"ThetaForge bridge VM is up",
                   f"thetaforge-bridge-vm RUNNING. Public IP: {ip}. "
                   f"SSH: ssh -i ~/.ssh/thetaforge_vm ubuntu@{ip}")
            return 0
        if state == "STARTING" or state == "PROVISIONING":
            log(f"attempt {attempt}: state={state}, waiting for RUNNING")
            time.sleep(20)
            continue

        log(f"attempt {attempt}: state={state}, calling START...")
        r = run_cli("compute", "instance", "action",
                    "--instance-id", instance_id, "--action", "START")
        msg = (r.stdout or r.stderr or "")[:200]
        if "TooManyRequests" in msg:
            backoff = min(backoff + 30, RETRY_MAX)
            log(f"  -> rate limited; backing off {backoff}s")
        elif "Out of host capacity" in r.stderr:
            backoff = min(backoff + 30, RETRY_MAX)
            log(f"  -> out of host capacity; backing off {backoff}s")
        elif msg and "lifecycle-state" in msg:
            backoff = RETRY_BASE
            log(f"  -> accepted ({msg.splitlines()[0][:80]})")
        time.sleep(backoff)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
        raise