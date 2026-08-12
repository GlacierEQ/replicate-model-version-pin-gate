from model_version_pin_gate import Decision, ModelVersionPinGate, ModelVersionPinGateRequest

V1 = "a" * 64
V2 = "b" * 64


def req(version=V1, environment="PRODUCTION", parameters=None, budget=4):
    return ModelVersionPinGateRequest(
        subject_id="job-1",
        payload={
            "model":"acme/image-gen",
            "version":version,
            "environment":environment,
            "parameters":parameters or {"steps":20},
            "input_digest":"c" * 64,
        },
        budget=budget,
    )


def test_production_explicit_pin_allows():
    r = ModelVersionPinGate().evaluate(req())
    assert r.decision is Decision.ALLOW
    assert r.result["resolved_version"] == V1
    assert r.result["resolution"] == "EXPLICIT_PIN"
    assert r.result["immutable_pin"] is True


def test_production_floating_alias_refuses_even_if_registry_can_resolve_it():
    gate = ModelVersionPinGate(aliases={"acme/image-gen":{"latest":V1}})
    r = gate.evaluate(req("latest"))
    assert r.decision is Decision.REFUSE
    assert "production_requires_immutable_version_pin" in r.reasons


def test_development_alias_resolves_to_visible_immutable_pin():
    gate = ModelVersionPinGate(aliases={"acme/image-gen":{"latest":V1}})
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
    gate = ModelVersionPinGate(known_versions={"acme/image-gen":[V2]})
    r = gate.evaluate(req(V1))
    assert r.decision is Decision.REFUSE
    assert "version_not_registered_for_model" in r.reasons


def test_parameter_change_changes_invocation_key():
    gate = ModelVersionPinGate()
    a = gate.evaluate(req(parameters={"steps":20}))
    b = gate.evaluate(req(parameters={"steps":30}))
    assert a.result["invocation_key"] != b.result["invocation_key"]
    assert a.metrics["reproducibility_fingerprint"] != b.metrics["reproducibility_fingerprint"]


def test_deterministic_replay():
    gate = ModelVersionPinGate()
    q = req()
    assert gate.evaluate(q) == gate.evaluate(q)


def test_work_budget_is_enforced():
    parameters = {f"p{i}": i for i in range(20)}
    r = ModelVersionPinGate().evaluate(req(parameters=parameters, budget=1.1))
    assert r.decision is Decision.REFUSE
    assert "work_budget_exceeded" in r.reasons
