#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from model_version_pin_gate import Decision, ModelVersionPinGate, ModelVersionPinGateRequest

VERSION = "a" * 64


def main() -> int:
    gate = ModelVersionPinGate(known_versions={"demo/image": [VERSION]}, clock=lambda: 1000)
    req = ModelVersionPinGateRequest(
        subject_id="operate-demo",
        payload={
            "model": "demo/image",
            "version": VERSION,
            "environment": "PRODUCTION",
            "parameters": {"steps": 28, "guidance": 7.5},
            "input_digest": "b" * 64,
        },
        budget=4,
        not_after=2000,
    )
    receipt = gate.evaluate(req)
    if receipt.decision is not Decision.ALLOW:
        print(json.dumps(receipt.as_dict(), indent=2, sort_keys=True))
        return 2

    with tempfile.TemporaryDirectory(prefix="model-pin-") as tmp:
        lock_path = Path(tmp) / "model-version.lock.json"
        lock = gate.write_lockfile(receipt, lock_path)
        loaded = gate.read_lockfile(lock_path)
        if loaded != lock or not gate.verify_lock(loaded):
            return 3

    output = {
        "receipt": receipt.as_dict(),
        "lock": lock.as_dict(),
        "verified": True,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
