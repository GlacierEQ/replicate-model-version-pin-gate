"""Deterministic model-version pinning for reproducible hosted inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


def _digest(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"


class Environment(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"


@dataclass(frozen=True)
class ModelVersionPinGateRequest:
    subject_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    budget: float = 4.0
    grant_id: str | None = None
    not_after: float | None = None


@dataclass(frozen=True)
class ModelVersionPinGateReceipt:
    decision: Decision
    reasons: tuple[str, ...]
    digest: str
    metrics: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "digest": self.digest,
            "metrics": self.metrics,
            "result": self.result,
        }


class ModelVersionPinGate:
    """Resolve model versions and forbid floating versions for production calls.

    Immutable version identifiers are 64-character hexadecimal digests. Aliases
    such as ``latest`` or ``stable`` may be resolved only outside production and
    only when the constructor is given an explicit alias registry. The receipt
    always exposes the resolved immutable version used to form the invocation key.
    """

    IMMUTABLE_VERSION_RE = re.compile(r"^[0-9a-fA-F]{64}$")
    VALID_PAYLOAD_KEYS = frozenset({"model", "version", "environment", "parameters", "input_digest"})
    MAX_PARAMETERS = 128
    BASE_WORK_UNITS = 1.0
    PARAMETER_WORK_UNITS = 0.05

    def __init__(
        self,
        *,
        aliases: Mapping[str, Mapping[str, str]] | None = None,
        known_versions: Mapping[str, Sequence[str]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._aliases = {
            str(model): {str(alias): str(version).lower() for alias, version in values.items()}
            for model, values in (aliases or {}).items()
        }
        self._known_versions = {
            str(model): frozenset(str(version).lower() for version in values)
            for model, values in (known_versions or {}).items()
        }
        self._clock = clock or time.time

    @staticmethod
    def _nonempty(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name}_missing")
        return value.strip()

    @staticmethod
    def _environment(value: Any) -> Environment:
        if not isinstance(value, str):
            raise ValueError("environment_missing")
        try:
            return Environment(value.strip().upper())
        except ValueError as exc:
            raise ValueError("environment_invalid") from exc

    @classmethod
    def is_immutable_version(cls, version: str) -> bool:
        return bool(cls.IMMUTABLE_VERSION_RE.fullmatch(version))

    def _refuse(self, req: ModelVersionPinGateRequest, reasons: list[str], result: dict[str, Any] | None = None) -> ModelVersionPinGateReceipt:
        unique = tuple(sorted(set(reasons)))
        result = result or {}
        body = {
            "subject_id": req.subject_id,
            "payload": req.payload,
            "budget": req.budget,
            "grant_id": req.grant_id,
            "not_after": req.not_after,
            "decision": Decision.REFUSE.value,
            "reasons": unique,
            "result": result,
        }
        return ModelVersionPinGateReceipt(
            decision=Decision.REFUSE,
            reasons=unique,
            digest=_digest(body),
            metrics={"bounded": True, "reason_count": len(unique)},
            result=result,
        )

    def evaluate(self, req: ModelVersionPinGateRequest) -> ModelVersionPinGateReceipt:
        if not isinstance(req, ModelVersionPinGateRequest):
            raise TypeError("req must be ModelVersionPinGateRequest")
        reasons: list[str] = []
        if not req.subject_id or not req.subject_id.strip():
            reasons.append("subject_id_missing")
        if not isinstance(req.budget, (int, float)) or isinstance(req.budget, bool) or not math.isfinite(float(req.budget)):
            reasons.append("budget_invalid")
        elif req.budget <= 0:
            reasons.append("budget_non_positive")
        if req.not_after is not None:
            if not isinstance(req.not_after, (int, float)) or isinstance(req.not_after, bool) or not math.isfinite(float(req.not_after)):
                reasons.append("request_expiry_invalid")
            elif self._clock() > float(req.not_after):
                reasons.append("request_expired")
        unknown = set(req.payload) - self.VALID_PAYLOAD_KEYS
        if unknown:
            reasons.append("payload_keys_unknown:" + ",".join(sorted(unknown)))
        if reasons:
            return self._refuse(req, reasons)

        try:
            model = self._nonempty(req.payload.get("model"), "model")
            requested_version = self._nonempty(req.payload.get("version"), "version")
            environment = self._environment(req.payload.get("environment"))
        except ValueError as exc:
            return self._refuse(req, [str(exc)])

        parameters = req.payload.get("parameters", {})
        if not isinstance(parameters, Mapping):
            return self._refuse(req, ["parameters_invalid"])
        if len(parameters) > self.MAX_PARAMETERS:
            return self._refuse(req, ["parameters_over_limit"])
        input_digest = req.payload.get("input_digest")
        if input_digest is not None:
            if not isinstance(input_digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", input_digest):
                return self._refuse(req, ["input_digest_invalid"])
            input_digest = input_digest.lower()

        immutable_input = self.is_immutable_version(requested_version)
        if environment is Environment.PRODUCTION and not immutable_input:
            return self._refuse(
                req,
                ["production_requires_immutable_version_pin"],
                {"model": model, "requested_version": requested_version, "environment": environment.value},
            )

        if immutable_input:
            resolved_version = requested_version.lower()
            resolution = "EXPLICIT_PIN"
        else:
            resolved_version = self._aliases.get(model, {}).get(requested_version)
            if not resolved_version:
                return self._refuse(
                    req,
                    ["floating_version_unresolved"],
                    {"model": model, "requested_version": requested_version, "environment": environment.value},
                )
            if not self.is_immutable_version(resolved_version):
                return self._refuse(req, ["alias_registry_target_not_immutable"])
            resolved_version = resolved_version.lower()
            resolution = "ALIAS_RESOLVED"

        known = self._known_versions.get(model)
        if known is not None and resolved_version not in known:
            return self._refuse(
                req,
                ["version_not_registered_for_model"],
                {"model": model, "resolved_version": resolved_version},
            )

        work_units = self.BASE_WORK_UNITS + len(parameters) * self.PARAMETER_WORK_UNITS
        if work_units > float(req.budget):
            return self._refuse(req, ["work_budget_exceeded"], {"work_units": work_units, "budget_units": float(req.budget)})

        parameter_fingerprint = _digest(dict(sorted(parameters.items())))
        invocation_key = _digest({
            "model": model,
            "version": resolved_version,
            "parameters": dict(sorted(parameters.items())),
            "input_digest": input_digest,
        })
        result = {
            "model": model,
            "environment": environment.value,
            "requested_version": requested_version,
            "resolved_version": resolved_version,
            "resolution": resolution,
            "immutable_pin": True,
            "invocation_key": invocation_key,
        }
        metrics = {
            "bounded": True,
            "parameter_count": len(parameters),
            "budget_units": float(req.budget),
            "work_units": work_units,
            "parameter_fingerprint": parameter_fingerprint,
            "reproducibility_fingerprint": _digest({
                "model": model,
                "resolved_version": resolved_version,
                "parameter_fingerprint": parameter_fingerprint,
                "input_digest": input_digest,
            }),
        }
        body = {
            "subject_id": req.subject_id,
            "grant_id": req.grant_id,
            "not_after": req.not_after,
            "decision": Decision.ALLOW.value,
            "result": result,
            "metrics": metrics,
        }
        return ModelVersionPinGateReceipt(
            decision=Decision.ALLOW,
            reasons=("immutable_model_version_bound",),
            digest=_digest(body),
            metrics=metrics,
            result=result,
        )

    @staticmethod
    def verify_receipt(receipt: ModelVersionPinGateReceipt) -> bool:
        return (
            isinstance(receipt, ModelVersionPinGateReceipt)
            and receipt.metrics.get("bounded") is True
            and len(receipt.digest) == 64
            and receipt.decision in {Decision.ALLOW, Decision.REFUSE}
            and (receipt.decision is Decision.REFUSE or receipt.result.get("immutable_pin") is True)
        )


Mechanism = ModelVersionPinGate


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and resolve a model-version pin request from JSON.")
    parser.add_argument("--input", "-i", help="JSON file; defaults to stdin")
    args = parser.parse_args(argv)
    try:
        raw = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise ValueError("request JSON must be an object")
        gate = ModelVersionPinGate(
            aliases=data.get("aliases") or {},
            known_versions=data.get("known_versions") or {},
        )
        req = ModelVersionPinGateRequest(
            subject_id=str(data.get("subject_id", "")),
            payload=dict(data.get("payload") or {}),
            budget=data.get("budget", 4.0),
            grant_id=data.get("grant_id"),
            not_after=data.get("not_after"),
        )
        receipt = gate.evaluate(req)
    except Exception as exc:
        print(json.dumps({"decision": "REFUSE", "reasons": [f"cli_input_error:{type(exc).__name__}:{exc}"]}, sort_keys=True))
        return 2
    print(json.dumps(receipt.as_dict(), indent=2, sort_keys=True))
    return 0 if receipt.decision is Decision.ALLOW else 2
