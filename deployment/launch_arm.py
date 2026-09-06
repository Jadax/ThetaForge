#!/usr/bin/env python3
"""Try to create thetaforge-advisor ARM instance in af-johannesburg-1.
Tries all valid sizes, one per 3 minutes, for up to 120 attempts (6 hours)."""
import json, os, subprocess, sys, time

os.environ["OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING"] = "True"

TENANCY = "ocid1.tenancy.oc1..aaaaaaaa5ptogdhsltwkrqf6sp4ygzkmutu6le4pt2eyccndqzevt5obroca"
AD = "vGbj:AF-JOHANNESBURG-1-AD-1"
IMAGE = "ocid1.image.oc1.af-johannesburg-1.aaaaaaaazydyampzoqjboi3hlyolbtcswevvregasjp36kgblt26upepmrgq"
SUBNET = "ocid1.subnet.oc1.af-johannesburg-1.aaaaaaaaleseezmie5zcinhaljnblzlfsiylgqfrlnccfsoedasf5i3wlwrq"
SIZES = [(2, 12), (2, 8), (1, 8), (1, 6), (1, 4)]
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arm_launch_status.json")
MAX_ATTEMPTS = 120
INTERVAL = 180  # 3 minutes

def get_ssh_key():
    path = os.path.expanduser("~/.ssh/thetaforge_vm.pub")
    with open(path) as f:
        return f.read().strip()

def try_launch(ocpus, mem, ssh_key):
    shape = json.dumps({"ocpus": ocpus, "memoryInGBs": mem})
    meta = json.dumps({"ssh_authorized_keys": ssh_key})
    cmd = [
        "oci", "compute", "instance", "launch",
        "-c", TENANCY,
        "--availability-domain", AD,
        "--display-name", "thetaforge-advisor",
        "--image-id", IMAGE,
        "--shape", "VM.Standard.A1.Flex",
        "--shape-config", shape,
        "--subnet-id", SUBNET,
        "--assign-public-ip", "true",
        "--metadata", meta,
        "--raw-output",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"

def main():
    ssh_key = get_ssh_key()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        for ocpus, mem in SIZES:
            print(f"[{ts}] Attempt {attempt}/{MAX_ATTEMPTS}: {ocpus} OCPU / {mem} GB...", flush=True)
            rc, stdout, stderr = try_launch(ocpus, mem, ssh_key)
            if rc == 0:
                data = json.loads(stdout)
                ip = data.get("vnic-attachments", [{}])[0].get("public-ip", "UNKNOWN")
                iid = data.get("id", "UNKNOWN")
                print(f"\n{'='*60}")
                print(f"SUCCESS! Instance created.")
                print(f"  Instance ID: {iid}")
                print(f"  Public IP:   {ip}")
                print(f"  Size:        {ocpus} OCPU / {mem} GB")
                print(f"{'='*60}\n")
                with open(STATUS_FILE, "w") as f:
                    json.dump({"status": "created", "instance_id": iid, "public_ip": ip,
                               "size": f"{ocpus}ocpu-{mem}gb", "attempt": attempt, "created_at": ts}, f, indent=2)
                print(f"\nNext: SSH to {ip} and run setup script")
                return 0
            err = stderr + stdout
            if "Out of host capacity" in err:
                print(f"  -> {ocpus}/{mem} capacity full", flush=True)
            elif "TooManyRequests" in err:
                print(f"  -> rate limited, waiting 60s...", flush=True)
                time.sleep(60)
            elif "LimitExceeded" in err:
                print(f"  -> {ocpus}/{mem} limit blocked", flush=True)
            else:
                short = err[:200].strip()
                print(f"  -> error: {short}", flush=True)
        print(f"  All sizes exhausted, waiting {INTERVAL}s...", flush=True)
        time.sleep(INTERVAL)
    print(f"FAILED after {MAX_ATTEMPTS} attempts", flush=True)
    with open(STATUS_FILE, "w") as f:
        json.dump({"status": "exhausted", "attempts": MAX_ATTEMPTS}, f, indent=2)
    return 1

if __name__ == "__main__":
    sys.exit(main())
