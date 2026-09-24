"""Launch a one-shot Google Cloud VM that builds the North America table.

The VM runs cloud/vm-build.sh from a pinned commit of this repo, uploads the
table, validation report, log and tiles to Cloud Storage, and deletes itself.
A hard maximum run duration (DELETE on expiry) is the backstop.

Usage (no gcloud needed; google-auth + REST):
  uv run --with google-auth --with requests python cloud/launch_gcp.py \\
      --key SERVICE_ACCOUNT.json --version na-2026.10.1 --commit <sha> [--dry-run]
  uv run --with google-auth --with requests python cloud/launch_gcp.py --key ... --status --version na-2026.10.1

Cost: n2-highmem-32 on demand is roughly $1.9/hour in us-central1; the
builds so far are estimated at 10-13 hours.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

ROOT = Path(__file__).resolve().parent.parent
COMPUTE = "https://compute.googleapis.com/compute/v1"
STORAGE = "https://storage.googleapis.com/storage/v1"


def session(key: Path):
    info = json.loads(key.read_text())
    creds = service_account.Credentials.from_service_account_file(
        str(key), scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return AuthorizedSession(creds), info["project_id"], info["client_email"]


def ensure_bucket(s, project: str, bucket: str, region: str) -> None:
    r = s.get(f"{STORAGE}/b/{bucket}")
    if r.status_code == 200:
        return
    body = {
        "name": bucket,
        "location": region.upper(),
        "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}},
        # Old builds (tiles especially) are only kept for incremental adds.
        "lifecycle": {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 400}}]},
    }
    r = s.post(f"{STORAGE}/b?project={project}", json=body)
    r.raise_for_status()
    print(f"created bucket gs://{bucket}")


def launch(args) -> None:
    s, project, sa = session(args.key)
    zone = args.zone
    region = zone.rsplit("-", 1)[0]
    bucket = args.bucket or f"{project}-drive-times"
    name = f"wrdt-build-{args.version}".lower().replace(".", "-")
    startup = (ROOT / "cloud" / "vm-build.sh").read_text()
    body = {
        "name": name,
        "machineType": f"zones/{zone}/machineTypes/{args.machine}",
        "disks": [
            {
                "boot": True,
                "autoDelete": True,
                "initializeParams": {
                    "sourceImage": "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64",
                    "diskSizeGb": str(args.disk_gb),
                    "diskType": f"zones/{zone}/diskTypes/pd-balanced",
                },
            }
        ],
        "networkInterfaces": [
            {"network": "global/networks/default", "accessConfigs": [{"type": "ONE_TO_ONE_NAT", "name": "External NAT"}]}
        ],
        "serviceAccounts": [{"email": sa, "scopes": ["https://www.googleapis.com/auth/cloud-platform"]}],
        "metadata": {
            "items": [
                {"key": "startup-script", "value": startup},
                {"key": "bucket", "value": bucket},
                {"key": "version", "value": args.version},
                {"key": "commit", "value": args.commit},
                {"key": "max-hours", "value": str(args.max_matrix_hours)},
                {"key": "reuse-graph", "value": args.reuse_graph},
            ]
        },
        "scheduling": {
            "maxRunDuration": {"seconds": str(int(args.max_run_hours * 3600))},
            "instanceTerminationAction": "DELETE",
            "automaticRestart": False,
            "onHostMaintenance": "TERMINATE",
        },
        "labels": {"purpose": "drive-table-build"},
    }
    print(f"project {project}, zone {zone}, {args.machine}, {args.disk_gb} GB, "
          f"max run {args.max_run_hours} h, pilot limit {args.max_matrix_hours} h")
    print(f"results -> gs://{bucket}/{args.version}/")
    if args.dry_run:
        print("dry run: not creating anything")
        return
    ensure_bucket(s, project, bucket, region)
    r = s.post(f"{COMPUTE}/projects/{project}/zones/{zone}/instances", json=body)
    if not r.ok:
        sys.exit(f"create failed: {r.status_code} {r.text[:500]}")
    print(f"creating instance {name}: operation {r.json().get('name')}")


def status(args) -> None:
    s, project, _ = session(args.key)
    bucket = args.bucket or f"{project}-drive-times"
    name = f"wrdt-build-{args.version}".lower().replace(".", "-")
    r = s.get(f"{COMPUTE}/projects/{project}/zones/{args.zone}/instances/{name}")
    print("instance:", r.json().get("status") if r.ok else f"none ({r.status_code})")
    for obj in ("STATUS", "pilot.json", "build.log"):
        r = s.get(f"{STORAGE}/b/{bucket}/o/{args.version}%2F{obj.replace('/', '%2F')}", params={"alt": "media"})
        if not r.ok:
            print(f"{obj}: -")
            continue
        text = r.text
        if obj == "build.log":
            lines = [l for l in text.splitlines() if l.startswith(("===", "STATUS", "pilot", "build "))]
            print("log markers:\n  " + "\n  ".join(lines[-12:]))
            print("log tail:\n  " + "\n  ".join(text.splitlines()[-5:]))
        else:
            print(f"{obj}: {text.strip()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--commit", default="")
    ap.add_argument("--zone", default="us-central1-a")
    ap.add_argument("--machine", default="n2-highmem-32")
    ap.add_argument("--disk-gb", type=int, default=450)
    ap.add_argument("--bucket", default="")
    ap.add_argument("--max-run-hours", type=float, default=18)
    ap.add_argument("--max-matrix-hours", type=float, default=12)
    ap.add_argument(
        "--reuse-graph",
        default="",
        help="gs:// prefix of a graph bundle saved by an earlier run (skips the ~1.5 h graph stage)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status(args)
    else:
        if not args.commit:
            sys.exit("--commit is required (the pushed commit the VM checks out)")
        launch(args)


if __name__ == "__main__":
    main()
