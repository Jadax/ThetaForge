#!/usr/bin/env python3
"""Persistent ARM instance launcher for af-johannesburg-1.
Runs on the always-on AMD VM. Tries all valid A1 sizes every 3 minutes."""
import oci
import json
import os
import sys
import time

TENANCY = "ocid1.tenancy.oc1..aaaaaaaa5ptogdhsltwkrqf6sp4ygzkmutu6le4pt2eyccndqzevt5obroca"
AD = "vGbj:AF-JOHANNESBURG-1-AD-1"
IMAGE = "ocid1.image.oc1.af-johannesburg-1.aaaaaaaazydyampzoqjboi3hlyolbtcswevvregasjp36kgblt26upepmrgq"
SUBNET = "ocid1.subnet.oc1.af-johannesburg-1.aaaaaaaaleseezmie5zcinhaljnblzlfsiylgqfrlnccfsoedasf5i3wlwrq"
SIZES = [(2, 12)]
SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHyi0ZIxA33MsAPDQGBvGRODgobLVErjPoPctTY4qYnI thetaforge-oracle-vm"
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arm_launch_status.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arm_retry_vm.log")
MAX_ATTEMPTS = 9999
INTERVAL = 600

# ntfy.sh push notification topic (unguessable). Install the ntfy app and
# subscribe to this topic to get pinged the moment the ARM instance launches.
NTFY_TOPIC = "thetaforge-arm-ozc7ebhka1345mgu6v8nwdqp"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def notify(title, message):
    """Push a ntfy.sh notification (best-effort; never raises)."""
    try:
        import subprocess
        subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-H", f"Title: {title}",
             "-d", message, NTFY_URL],
            timeout=20,
        )
        log(f"NOTIFY sent: {title}")
    except Exception as e:
        log(f"NOTIFY failed: {e}")


def main():
    config = oci.config.from_file()
    compute = oci.core.ComputeClient(config)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        for ocpus, mem in SIZES:
            log(f"Attempt {attempt}/{MAX_ATTEMPTS}: {ocpus} OCPU / {mem} GB...")
            try:
                resp = compute.launch_instance(
                    oci.core.models.LaunchInstanceDetails(
                        compartment_id=TENANCY,
                        availability_domain=AD,
                        display_name="thetaforge-advisor",
                        shape="VM.Standard.A1.Flex",
                        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                            ocpus=ocpus,
                            memory_in_gbs=mem,
                        ),
                        source_details=oci.core.models.InstanceSourceViaImageDetails(
                            image_id=IMAGE,
                        ),
                        subnet_id=SUBNET,
                        create_vnic_details=oci.core.models.CreateVnicDetails(
                            assign_public_ip=True,
                        ),
                        metadata={"ssh_authorized_keys": SSH_KEY},
                    )
                )
                inst = resp.data
                log(f"INSTANCE CREATED! ID={inst.id} State={inst.lifecycle_state}")
                notify("ARM instance created",
                       f"ThetaForge ARM instance launched! ID={inst.id} "
                       f"State={inst.lifecycle_state} ({ocpus} OCPU / {mem} GB). "
                       f"Waiting for public IP...")

                # Poll until running and get IP
                ip = None
                for _ in range(60):
                    time.sleep(5)
                    check = compute.get_instance(inst.id).data
                    if check.lifecycle_state == "RUNNING":
                        attachments = compute.list_vnic_attachments(TENANCY, instance_id=inst.id).data
                        for att in attachments:
                            try:
                                vnic = compute.get_vnic(att.vnic_id).data
                                if vnic.public_ip:
                                    ip = vnic.public_ip
                                    break
                            except Exception:
                                pass
                        break
                    elif check.lifecycle_state == "TERMINATED":
                        log(f"Instance terminated during boot!")
                        break

                log(f"Public IP: {ip}")
                with open(STATUS_FILE, "w") as f:
                    json.dump({
                        "status": "created",
                        "instance_id": inst.id,
                        "public_ip": ip,
                        "size": f"{ocpus}ocpu-{mem}gb",
                        "attempt": attempt,
                    }, f, indent=2)
                log("SUCCESS - exiting")
                notify("ARM instance RUNNING",
                       f"ThetaForge ARM advisor is up! Public IP: {ip} "
                       f"(ID={inst.id}). SSH: ssh -i ~/.ssh/thetaforge_vm ubuntu@{ip}")
                return 0

            except oci.exceptions.ServiceError as e:
                msg = str(e)
                if "Out of host capacity" in msg:
                    log(f"  -> {ocpus}/{mem} capacity full")
                elif "TooManyRequests" in msg or e.status == 429:
                    log(f"  -> rate limited, skipping rest of round")
                    break
                elif "LimitExceeded" in msg:
                    log(f"  -> {ocpus}/{mem} limit blocked")
                else:
                    log(f"  -> OCI error {e.status}: {e.message[:150]}")
            except Exception as e:
                log(f"  -> unexpected: {e}")

            time.sleep(60)

        log(f"All sizes tried, sleeping {INTERVAL}s...")
        time.sleep(INTERVAL)

    log(f"FAILED after {MAX_ATTEMPTS} attempts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
