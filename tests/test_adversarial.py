from model_version_pin_gate import Decision, ModelVersionPinGate, ModelVersionPinGateRequest

V1 = "d" * 64


def base(payload_update=None, **request_update):
    payload = {
        "model":"acme/text",
        "version":V1,
        "environment":"PRODUCTION",
        "parameters":{},
    }
    payload.update(payload_update or {})
    values = dict(subject_id="adv", payload=payload, budget=4)
    values.update(request_update)
    return ModelVersionPinGateRequest(**values)


def test_latest_tag_cannot_enter_production():
    r = ModelVersionPinGate(aliases={"acme/text":{"latest":V1}}).evaluate(base({"version":"latest"}))
    assert r.decision is Decision.REFUSE


def test_partial_hash_is_not_immutable_pin():
    r = ModelVersionPinGate().evaluate(base({"version":"d"*12}))
    assert r.decision is Decision.REFUSE
    assert "production_requires_immutable_version_pin" in r.reasons


def test_alias_registry_cannot_resolve_alias_to_another_alias():
    gate = ModelVersionPinGate(aliases={"acme/text":{"stable":"latest"}})
    r = gate.evaluate(base({"version":"stable","environment":"STAGING"}))
    assert r.decision is Decision.REFUSE
    assert "alias_registry_target_not_immutable" in r.reasons


def test_unknown_policy_field_refuses():
    r = ModelVersionPinGate().evaluate(base({"ignore_version_pin":True}))
    assert r.decision is Decision.REFUSE
    assert any(x.startswith("payload_keys_unknown:") for x in r.reasons)


def test_bad_input_digest_refuses():
    r = ModelVersionPinGate().evaluate(base({"input_digest":"not-a-digest"}))
    assert r.decision is Decision.REFUSE
    assert "input_digest_invalid" in r.reasons


def test_request_expiry_refuses():
    r = ModelVersionPinGate(clock=lambda:101).evaluate(base(not_after=100))
    assert r.decision is Decision.REFUSE
    assert "request_expired" in r.reasons


def test_boolean_budget_refuses():
    r = ModelVersionPinGate().evaluate(base(budget=True))
    assert r.decision is Decision.REFUSE
    assert "budget_invalid" in r.reasons
