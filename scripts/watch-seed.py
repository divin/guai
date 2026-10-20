#!/usr/bin/env python3
"""Seed the watchface's pypkjs localStorage with server URL + token.

Writes GUAI_SERVER / GUAI_TOKEN into the emulator's persist dir for the
watchface UUID so pkjs is configured without the interactive config page
(which is flaky under pebble-tool). Non-interactive.

Usage:  just watch-config          (with the emulator stopped)
Then:   just watch-emulate         (pkjs reads the seeded values and uploads)

Requires server/.env with GUAI_SYNC_TOKEN. Override the server URL with
GUAI_SERVER (default http://127.0.0.1:8000).
"""

import dbm.dumb
import os
import re
import subprocess
import sys

# package.json uuids of the GUAI apps (watchface, check-in app)
UUIDS = [
    "d2637251-644a-4bdb-88fa-41df52f2a444",  # watchface
    "70f54c97-07c2-404e-b093-37dd80c5f4cd",  # check-in app
]
PLATFORM = "emery"


def active_sdk():
    out = subprocess.run(["pebble", "--version"], capture_output=True, text=True).stdout
    match = re.search(r"active SDK: v?([0-9][0-9.]*)", out)
    if not match:
        sys.exit("could not determine the active SDK (pebble --version)")
    return match.group(1)


def read_env(path, key):
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except (FileNotFoundError, OSError):
        return None
    return None


def main():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    token = read_env(os.path.join(repo, "server", ".env"), "GUAI_SYNC_TOKEN")
    if not token:
        sys.exit("GUAI_SYNC_TOKEN not found in server/.env (copy ../example.env first)")
    server = os.environ.get("GUAI_SERVER", "http://127.0.0.1:8000")

    base = os.path.expanduser("~/Library/Application Support/Pebble SDK")
    for uuid in UUIDS:
        db_path = os.path.join(base, active_sdk(), PLATFORM, "localstorage", uuid)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        db = dbm.dumb.open(db_path, "c")
        db["GUAI_SERVER"] = server
        db["GUAI_TOKEN"] = token
        db.close()

        print("seeded localStorage -> %s" % db_path)
    print("  GUAI_SERVER=%s" % server)
    print("  GUAI_TOKEN=<%d chars> (from server/.env)" % len(token))


if __name__ == "__main__":
    main()