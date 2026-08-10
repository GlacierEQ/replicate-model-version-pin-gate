"""Model Version Pin Gate — SCAFFOLD STUB.

Company lens: Replicate (independent; no affiliation).
Bottleneck: Hosted model invocations drift versions without caller-visible pins.

IMPLEMENTATION: see DEV_UP_INSTRUCTIONS.md
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


def _digest(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REFUSE = "REFUSE"


@dataclass(frozen=True)
class ModelVersionPinGateRequest:
    """Input envelope — expand fields as the mechanism solidifies."""

    subject_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    budget: float = 1.0
    # Authority / freshness placeholders for the filling AI:
    grant_id: str | None = None
    not_after: float | None = None


@dataclass(frozen=True)
class ModelVersionPinGateReceipt:
    decision: Decision
    reasons: tuple[str, ...]
    digest: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "digest": self.digest,
            "metrics": self.metrics,
        }


class ModelVersionPinGate:
    """Central mechanism stub.

    Contract the filling AI must preserve:
    - `evaluate(req)` returns a ModelVersionPinGateReceipt
    - invalid/empty subject_id → REFUSE
    - budget <= 0 → REFUSE
    - otherwise ALLOW with a content digest over the request
    Replace body with the real algorithm; keep fail-closed edges.
    """

    # Named constants (no magic numbers)
    MIN_BUDGET: float = 0.0
    MAX_REASON_LEN: int = 240

    def evaluate(self, req: ModelVersionPinGateRequest) -> ModelVersionPinGateReceipt:
        reasons: list[str] = []
        if not req.subject_id or not str(req.subject_id).strip():
            reasons.append("subject_id_missing")
        if req.budget <= self.MIN_BUDGET:
            reasons.append("budget_non_positive")
        # Scaffold: treat missing grant as soft signal only; real impl may hard-refuse.
        if reasons:
            body = {
                "subject_id": req.subject_id,
                "payload": req.payload,
                "budget": req.budget,
                "decision": Decision.REFUSE.value,
                "reasons": reasons,
            }
            return ModelVersionPinGateReceipt(
                decision=Decision.REFUSE,
                reasons=tuple(reasons),
                digest=_digest(body),
                metrics={"scaffold": True, "reason_count": len(reasons)},
            )

        body = {
            "subject_id": req.subject_id,
            "payload": req.payload,
            "budget": req.budget,
            "grant_id": req.grant_id,
            "decision": Decision.ALLOW.value,
        }
        return ModelVersionPinGateReceipt(
            decision=Decision.ALLOW,
            reasons=("scaffold_allow",),
            digest=_digest(body),
            metrics={
                "scaffold": True,
                "payload_keys": sorted(req.payload.keys()),
                "budget": req.budget,
            },
        )


# Friendly alias for operate scripts
Mechanism = ModelVersionPinGate
