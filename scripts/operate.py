#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from model_version_pin_gate import Decision, ModelVersionPinGate, ModelVersionPinGateRequest

VERSION="a"*64

def main()->int:
    gate=ModelVersionPinGate(known_versions={"demo/image":[VERSION]}, clock=lambda:1000)
    req=ModelVersionPinGateRequest(
        subject_id="operate-demo",
        payload={
            "model":"demo/image", "version":VERSION, "environment":"PRODUCTION",
            "parameters":{"steps":28,"guidance":7.5}, "input_digest":"b"*64,
        }, budget=4, not_after=2000,
    )
    receipt=gate.evaluate(req)
    print(json.dumps(receipt.as_dict(),indent=2,sort_keys=True))
    if receipt.decision is not Decision.ALLOW:return 2
    if receipt.result["resolved_version"] != VERSION:return 3
    if receipt.result["resolution"] != "EXPLICIT_PIN":return 4
    if not gate.verify_receipt(receipt):return 5
    return 0

if __name__=="__main__": raise SystemExit(main())
