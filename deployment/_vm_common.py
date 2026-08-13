"""Shared helpers for the VM's standalone deployment scripts.

vm_auto_executor.py and vm_auto_manager.py are copied flat onto the VM
(WorkingDirectory=/opt/thetaforge-bridge, no agents/ package tree
available there -- see docs/AUTONOMOUS_TRADING.md), so they can't import
from the main repo. They CAN import from each other's directory though,
since Python puts a script's own directory on sys.path -- this module gets
copied alongside them for exactly that reason. Functions take their
dependencies (client, headers, logger, script path) as parameters rather
than reading module-level globals, so this stays a plain, testable utility
with no hidden coupling to either caller's env-var setup.
"""
import logging
import subprocess
from pathlib import Path

import httpx


def is_market_open(client: httpx.Client, advisor_url: str, advisor_headers: dict, logger: logging.Logger) -> bool:
    try:
        response = client.get(f"{advisor_url}/api/advisor/scanner/status", headers=advisor_headers, timeout=10)
        response.raise_for_status()
        return bool(response.json().get("market_open"))
    except Exception:
        logger.exception("Could not read /scanner/status; treating as closed for this cycle")
        return False


def run_journal_sync(script_path: str, logger: logging.Logger) -> None:
    if not Path(script_path).exists():
        logger.warning("Journal sync script not found at %s; skipping", script_path)
        return
    try:
        result = subprocess.run([script_path], capture_output=True, text=True, timeout=120)
        logger.info("journal sync: %s", result.stdout.strip() or result.stderr.strip())
    except Exception:
        logger.exception("Journal sync failed")
