"""Deterministic model-version pinning and lockfiles for reproducible inference."""
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
    """Hash canonical JSON only. Unsupported Python objects are never coerced."""
    payload = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json(value: Any, *, path: str = "value") -> Any:
    """Validate and copy the strict JSON value domain used for fingerprints."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}_non_finite")
        return value
    if isinstance(value, list):
        return [_canonical_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}_key_not_string")
            out[key] = _canonical_json(item, path=f"{path}.{key}")
        return out
    raise ValueError(f"{path}_not_canonical_json")


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


@dataclass(frozen=True)
class ModelVersionLock:
    """Portable content-addressed record of an exact model invocation identity."""

    schema_version: int
    model: str
    resolved_version: str
    environment: str
    invocation_key: str
    parameter_fingerprint: str
    reproducibility_fingerprint: str
    input_digest: str | None
    receipt_digest: str
    lock_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model": self.model,
            "resolved_version": self.resolved_version,
            "environment": self.environment,
            "invocation_key": self.invocation_key,
            "parameter_fingerprint": self.parameter_fingerprint,
            "reproducibility_fingerprint": self.reproducibility_fingerprint,
            "input_digest": self.input_digest,
            "receipt_digest": self.receipt_digest,
            "lock_digest": self.lock_digest,
        }


class ModelVersionPinGate:
    """Resolve model versions and forbid floating versions for production calls."""

    IMMUTABLE_VERSION_RE = re.compile(r"^[0-9a-fA-F]{64}$")
    VALID_PAYLOAD_KEYS = frozenset(
        {"model", "version", "environment", "parameters", "input_digest"}
    )
    MAX_PARAMETERS = 128
    BASE_WORK_UNITS = 1.0
    PARAMETER_WORK_UNITS = 0.05
    LOCK_SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        aliases: Mapping[str, Mapping[str, str]] | None = None,
        known_versions: Mapping[str, Sequence[str]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._aliases = {
            str(model): {
                str(alias): str(version).lower() for alias, version in values.items()
            }
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

    @staticmethod
    def _receipt_digest(
        decision: Decision,
        reasons: Sequence[str],
        metrics: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> str:
        return _digest(
            {
                "decision": decision.value,
                "reasons": list(reasons),
                "metrics": dict(metrics),
                "result": dict(result),
            }
        )

    def _refuse(
        self,
        req: ModelVersionPinGateRequest,
        reasons: list[str],
        result: dict[str, Any] | None = None,
    ) -> ModelVersionPinGateReceipt:
        unique = tuple(sorted(set(reasons)))
        result = _canonical_json(result or {}, path="result")
        metrics = {"bounded": True, "reason_count": len(unique)}
        digest = self._receipt_digest(Decision.REFUSE, unique, metrics, result)
        return ModelVersionPinGateReceipt(
            decision=Decision.REFUSE,
            reasons=unique,
            digest=digest,
            metrics=metrics,
            result=result,
        )

    def evaluate(
        self, req: ModelVersionPinGateRequest
    ) -> ModelVersionPinGateReceipt:
        if not isinstance(req, ModelVersionPinGateRequest):
            raise TypeError("req must be ModelVersionPinGateRequest")

        reasons: list[str] = []
        if not req.subject_id or not req.subject_id.strip():
            reasons.append("subject_id_missing")
        if (
            not isinstance(req.budget, (int, float))
            or isinstance(req.budget, bool)
            or not math.isfinite(float(req.budget))
        ):
            reasons.append("budget_invalid")
        elif req.budget <= 0:
            reasons.append("budget_non_positive")
        if req.not_after is not None:
            if (
                not isinstance(req.not_after, (int, float))
                or isinstance(req.not_after, bool)
                or not math.isfinite(float(req.not_after))
            ):
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
        try:
            canonical_parameters = _canonical_json(parameters, path="parameters")
        except ValueError as exc:
            return self._refuse(req, [str(exc)])

        input_digest = req.payload.get("input_digest")
        if input_digest is not None:
            if not isinstance(input_digest, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", input_digest
            ):
                return self._refuse(req, ["input_digest_invalid"])
            input_digest = input_digest.lower()

        immutable_input = self.is_immutable_version(requested_version)
        if environment is Environment.PRODUCTION and not immutable_input:
            return self._refuse(
                req,
                ["production_requires_immutable_version_pin"],
                {
                    "model": model,
                    "requested_version": requested_version,
                    "environment": environment.value,
                },
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
                    {
                        "model": model,
                        "requested_version": requested_version,
                        "environment": environment.value,
                    },
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
            return self._refuse(
                req,
                ["work_budget_exceeded"],
                {"work_units": work_units, "budget_units": float(req.budget)},
            )

        parameter_fingerprint = _digest(canonical_parameters)
        invocation_key = _digest(
            {
                "model": model,
                "version": resolved_version,
                "parameters": canonical_parameters,
                "input_digest": input_digest,
            }
        )
        reproducibility_fingerprint = _digest(
            {
                "model": model,
                "resolved_version": resolved_version,
                "parameter_fingerprint": parameter_fingerprint,
                "input_digest": input_digest,
            }
        )
        result = {
            "model": model,
            "environment": environment.value,
            "requested_version": requested_version,
            "resolved_version": resolved_version,
            "resolution": resolution,
            "immutable_pin": True,
            "invocation_key": invocation_key,
            "input_digest": input_digest,
        }
        metrics = {
            "bounded": True,
            "parameter_count": len(parameters),
            "budget_units": float(req.budget),
            "work_units": work_units,
            "parameter_fingerprint": parameter_fingerprint,
            "reproducibility_fingerprint": reproducibility_fingerprint,
        }
        reasons = ("immutable_model_version_bound",)
        digest = self._receipt_digest(Decision.ALLOW, reasons, metrics, result)
        return ModelVersionPinGateReceipt(
            decision=Decision.ALLOW,
            reasons=reasons,
            digest=digest,
            metrics=metrics,
            result=result,
        )

    @classmethod
    def verify_receipt(cls, receipt: ModelVersionPinGateReceipt) -> bool:
        if not isinstance(receipt, ModelVersionPinGateReceipt):
            return False
        if receipt.decision not in {Decision.ALLOW, Decision.REFUSE}:
            return False
        if receipt.metrics.get("bounded") is not True:
            return False
        if receipt.decision is Decision.ALLOW and receipt.result.get("immutable_pin") is not True:
            return False
        try:
            expected = cls._receipt_digest(
                receipt.decision, receipt.reasons, receipt.metrics, receipt.result
            )
        except (TypeError, ValueError):
            return False
        return expected == receipt.digest

    @classmethod
    def build_lock(cls, receipt: ModelVersionPinGateReceipt) -> ModelVersionLock:
        """Create a deterministic lock from a self-consistent successful receipt."""
        if receipt.decision is not Decision.ALLOW or not cls.verify_receipt(receipt):
            raise ValueError("lock_requires_verified_allow_receipt")
        result = receipt.result
        metrics = receipt.metrics
        body = {
            "schema_version": cls.LOCK_SCHEMA_VERSION,
            "model": result["model"],
            "resolved_version": result["resolved_version"],
            "environment": result["environment"],
            "invocation_key": result["invocation_key"],
            "parameter_fingerprint": metrics["parameter_fingerprint"],
            "reproducibility_fingerprint": metrics["reproducibility_fingerprint"],
            "input_digest": result.get("input_digest"),
            "receipt_digest": receipt.digest,
        }
        return ModelVersionLock(**body, lock_digest=_digest(body))

    @classmethod
    def verify_lock(
        cls,
        lock: Mapping[str, Any] | ModelVersionLock,
        *,
        expected_digest: str | None = None,
    ) -> bool:
        """Verify lock checksum and, when supplied, an independently stored digest.

        The embedded ``lock_digest`` detects accidental corruption. Supplying
        ``expected_digest`` additionally binds the lock to a value obtained from
        a trusted external channel or immutable manifest, so a rewritten lock
        with a recomputed embedded checksum is rejected.
        """
        data = lock.as_dict() if isinstance(lock, ModelVersionLock) else dict(lock)
        embedded = data.pop("lock_digest", None)
        if not isinstance(embedded, str) or len(embedded) != 64:
            return False
        if data.get("schema_version") != cls.LOCK_SCHEMA_VERSION:
            return False
        version = data.get("resolved_version")
        if not isinstance(version, str) or not cls.is_immutable_version(version):
            return False
        for key in (
            "model",
            "environment",
            "invocation_key",
            "parameter_fingerprint",
            "reproducibility_fingerprint",
            "receipt_digest",
        ):
            if not isinstance(data.get(key), str) or not data[key]:
                return False
        try:
            computed = _digest(data)
        except (TypeError, ValueError):
            return False
        if computed != embedded:
            return False
        if expected_digest is not None:
            if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_digest):
                return False
            if computed != expected_digest.lower():
                return False
        return True

    @classmethod
    def write_lockfile(
        cls, receipt: ModelVersionPinGateReceipt, path: str | Path
    ) -> ModelVersionLock:
        lock = cls.build_lock(receipt)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(lock.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return lock

    @classmethod
    def read_lockfile(
        cls,
        path: str | Path,
        *,
        expected_digest: str | None = None,
    ) -> ModelVersionLock:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, Mapping) or not cls.verify_lock(
            data, expected_digest=expected_digest
        ):
            raise ValueError("invalid_model_version_lock")
        return ModelVersionLock(**dict(data))


Mechanism = ModelVersionPinGate


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate model-version pins and create reproducible lockfiles."
    )
    parser.add_argument("--input", "-i", help="request JSON file; defaults to stdin")
    parser.add_argument("--lockfile", help="write a lockfile for an allowed request")
    parser.add_argument(
        "--verify-lockfile",
        help="verify an existing lockfile instead of evaluating a request",
    )
    parser.add_argument(
        "--expected-digest",
        help="trusted external SHA-256 digest to bind lockfile verification",
    )
    args = parser.parse_args(argv)

    if args.verify_lockfile:
        try:
            lock = ModelVersionPinGate.read_lockfile(
                args.verify_lockfile, expected_digest=args.expected_digest
            )
        except Exception as exc:
            print(
                json.dumps(
                    {"valid": False, "reason": f"{type(exc).__name__}:{exc}"},
                    sort_keys=True,
                )
            )
            return 2
        print(
            json.dumps(
                {
                    "valid": True,
                    "integrity": "external_digest_bound" if args.expected_digest else "embedded_checksum",
                    "lock": lock.as_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

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
        lock = None
        if args.lockfile and receipt.decision is Decision.ALLOW:
            lock = gate.write_lockfile(receipt, args.lockfile)
    except Exception as exc:
        print(
            json.dumps(
                {"decision": "REFUSE", "reasons": [f"cli_input_error:{type(exc).__name__}:{exc}"]},
                sort_keys=True,
            )
        )
        return 2

    output = receipt.as_dict()
    if lock is not None:
        output["lock_digest"] = lock.lock_digest
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if receipt.decision is Decision.ALLOW else 2
