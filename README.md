# Model Version Pin

A portable, vendor-neutral runtime for binding hosted-model calls to immutable versions and producing reproducible lockfiles.

> Independent GlacierEQ implementation. Not affiliated with, endorsed by, employed by, or deployed at Replicate.

## Purpose

A model call should be reproducible later. That requires more than remembering a model name: the exact immutable version, normalized parameters, input identity, and invocation fingerprint must survive outside process memory.

This package turns those facts into a deterministic evaluation receipt and a content-addressed `model-version.lock.json` artifact.

## Capabilities

- production rejects floating aliases and requires an explicit 64-hex immutable version identifier
- development/staging aliases resolve only through an explicit caller-supplied registry
- optional model/version registries reject pins associated with the wrong model
- parameter and input fingerprints are part of a deterministic invocation key
- request expiry, parameter count, and work budget are enforced
- unknown request fields fail closed
- successful receipts can be materialized as deterministic lockfiles
- lockfiles contain the immutable version, invocation key, fingerprints, receipt digest, and their own integrity digest
- lockfile verification rejects mutation or malformed version identity
- refused requests cannot produce lockfiles

## Install

```bash
python -m pip install .
```

## CLI

Evaluate a request from JSON:

```bash
model-version-pin --input request.json
```

Evaluate and persist the exact invocation identity:

```bash
model-version-pin --input request.json --lockfile model-version.lock.json
```

Verify a saved lock:

```bash
model-version-pin --verify-lockfile model-version.lock.json
```

The command exits non-zero for refused requests, malformed input, or invalid lockfiles.

## Verify the repository

```bash
python -m pytest -q
python scripts/operate.py
```

`operate.py` exercises the complete local path: production pin validation, deterministic receipt generation, lockfile persistence, lockfile readback, and integrity verification.

## Provider boundary

This repository deliberately does not pretend to possess provider production credentials. Provider adapters may populate `aliases` and `known_versions` from real APIs, but the invariant enforced here is independent of any one hosted-model vendor: **production execution identity must resolve to an immutable version and remain reproducible afterward.**
