from model_version_pin_gate import (
    Decision,
    ModelVersionPinGate,
    ModelVersionPinGateReceipt,
    ModelVersionPinGateRequest,
)

V1 = "a" * 64
V2 = "b" * 64


def req(version=V1, environment="PRODUCTION", parameters=None, budget=4):
    return ModelVersionPinGateRequest(
        subject_id="job-1",
        payload={
            "model": "acme/image-gen",
            "version": version,
            "environment": environment,
            "parameters": {"steps": 20} if parameters is None else parameters,
            "input_digest": "c" * 64,
        },
        budget=budget,
    )


def test_production_explicit_pin_allows():
    r = ModelVersionPinGate().evaluate(req())
    assert r.decision is Decision.ALLOW
    assert r.result["resolved_version"] == V1
    assert r.result["resolution"] == "EXPLICIT_PIN"
    assert r.result["immutable_pin"] is True
    assert ModelVersionPinGate.verify_receipt(r)


def test_production_floating_alias_refuses_even_if_registry_can_resolve_it():
    gate = ModelVersionPinGate(aliases={"acme/image-gen": {"latest": V1}})
    r = gate.evaluate(req("latest"))
    assert r.decision is Decision.REFUSE
    assert "production_requires_immutable_version_pin" in r.reasons


def test_development_alias_resolves_to_visible_immutable_pin():
    gate = ModelVersionPinGate(aliases={"acme/image-gen": {"latest": V1}})
    r = gate.evaluate(req("latest", "DEVELOPMENT"))
    assert r.decision is Decision.ALLOW
    assert r.result["requested_version"] == "latest"
    assert r.result["resolved_version"] == V1
    assert r.result["resolution"] == "ALIAS_RESOLVED"


def test_unknown_alias_refuses():
    r = ModelVersionPinGate().evaluate(req("latest", "DEVELOPMENT"))
    assert r.decision is Decision.REFUSE
    assert "floating_version_unresolved" in r.reasons


def test_known_version_registry_catches_wrong_model_version():
    gate = ModelVersionPinGate(known_versions={"acme/image-gen": [V2]})
    r = gate.evaluate(req(V1))
    assert r.decision is Decision.REFUSE
    assert "version_not_registered_for_model" in r.reasons


def test_parameter_change_changes_invocation_key():
    gate = ModelVersionPinGate()
    a = gate.evaluate(req(parameters={"steps": 20}))
    b = gate.evaluate(req(parameters={"steps": 30}))
    assert a.result["invocation_key"] != b.result["invocation_key"]
    assert a.metrics["reproducibility_fingerprint"] != b.metrics["reproducibility_fingerprint"]


def test_parameters_must_be_canonical_json():
    gate = ModelVersionPinGate()
    receipt = gate.evaluate(req(parameters={"bad": {1, 2}}))
    assert receipt.decision is Decision.REFUSE
    assert "parameters.bad_not_canonical_json" in receipt.reasons


def test_non_string_parameter_keys_are_refused():
    gate = ModelVersionPinGate()
    receipt = gate.evaluate(req(parameters={1: "bad"}))
    assert receipt.decision is Decision.REFUSE
    assert "parameters_key_not_string" in receipt.reasons


def test_deterministic_replay():
    gate = ModelVersionPinGate()
    q = req()
    assert gate.evaluate(q) == gate.evaluate(q)


def test_work_budget_is_enforced():
    parameters = {f"p{i}": i for i in range(20)}
    r = ModelVersionPinGate().evaluate(req(parameters=parameters, budget=1.1))
    assert r.decision is Decision.REFUSE
    assert "work_budget_exceeded" in r.reasons


def test_lockfile_round_trip_is_content_addressed(tmp_path):
    gate = ModelVersionPinGate()
    receipt = gate.evaluate(req())
    lock_path = tmp_path / "model-version.lock.json"

    lock = gate.write_lockfile(receipt, lock_path)
    loaded = gate.read_lockfile(lock_path, expected_digest=lock.lock_digest)

    assert loaded == lock
    assert gate.verify_lock(loaded, expected_digest=lock.lock_digest)
    assert loaded.resolved_version == V1
    assert loaded.invocation_key == receipt.result["invocation_key"]
    assert loaded.receipt_digest == receipt.digest


def test_embedded_checksum_detects_accidental_corruption(tmp_path):
    gate = ModelVersionPinGate()
    receipt = gate.evaluate(req())
    lock_path = tmp_path / "model-version.lock.json"
    gate.write_lockfile(receipt, lock_path)

    import json

    data = json.loads(lock_path.read_text())
    data["resolved_version"] = V2
    lock_path.write_text(json.dumps(data))

    try:
        gate.read_lockfile(lock_path)
    except ValueError as exc:
        assert str(exc) == "invalid_model_version_lock"
    else:
        raise AssertionError("corrupted lockfile was accepted")


def test_external_digest_rejects_semantic_rewrite_with_recomputed_checksum():
    import hashlib
    import json

    gate = ModelVersionPinGate()
    receipt = gate.evaluate(req())
    lock = gate.build_lock(receipt)
    trusted_digest = lock.lock_digest
    data = lock.as_dict()
    data["resolved_version"] = V2

    body = dict(data)
    body.pop("lock_digest")
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    data["lock_digest"] = hashlib.sha256(canonical.encode()).hexdigest()

    # The embedded checksum alone can only prove internal consistency.
    assert gate.verify_lock(data) is True
    # The independently retained digest binds the original semantic identity.
    assert gate.verify_lock(data, expected_digest=trusted_digest) is False


def test_fabricated_or_modified_receipt_cannot_be_locked():
    gate = ModelVersionPinGate()
    receipt = gate.evaluate(req())
    result = dict(receipt.result)
    result["resolved_version"] = V2
    forged = ModelVersionPinGateReceipt(
        decision=receipt.decision,
        reasons=receipt.reasons,
        digest=receipt.digest,
        metrics=receipt.metrics,
        result=result,
    )
    assert gate.verify_receipt(forged) is False
    try:
        gate.build_lock(forged)
    except ValueError as exc:
        assert str(exc) == "lock_requires_verified_allow_receipt"
    else:
        raise AssertionError("forged receipt produced a lock")


def test_refused_receipt_cannot_be_locked():
    gate = ModelVersionPinGate()
    receipt = gate.evaluate(req("latest", "PRODUCTION"))
    assert receipt.decision is Decision.REFUSE

    try:
        gate.build_lock(receipt)
    except ValueError as exc:
        assert str(exc) == "lock_requires_verified_allow_receipt"
    else:
        raise AssertionError("refused receipt produced a lock")
