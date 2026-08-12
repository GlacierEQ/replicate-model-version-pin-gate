# Model Version Pin

A portable, vendor-neutral runtime for binding hosted-model calls to immutable versions and producing reproducible lockfiles.

> Independent GlacierEQ implementation. Not affiliated with, endorsed by, employed by, or deployed at Replicate.

## Purpose

A model call should be reproducible later. That requires more than remembering a model name: the exact immutable version, canonical parameters, input identity, and invocation fingerprint must survive outside process memory.

This package turns those facts into a deterministic evaluation receipt and a content-addressed `model-version.lock.json` artifact.

## Capabilities

- production rejects floating aliases and requires an explicit 64-hex immutable version identifier
- development/staging aliases resolve only through an explicit caller-supplied registry
- optional model/version registries reject pins associated with the wrong model
- parameters must be canonical JSON; sets, object instances, non-string mapping keys, and non-finite numbers are refused instead of stringified
- parameter and input fingerprints are part of a deterministic invocation key
- request expiry, parameter count, and work budget are enforced
- unknown request fields fail closed
- receipt digests are recomputable from receipt contents, so modified or fabricated receipts cannot retain a valid original digest
- successful receipts can be materialized as deterministic lockfiles
- each lockfile contains the immutable version, invocation key, fingerprints, receipt digest, and an embedded SHA-256 checksum
- the embedded checksum detects accidental corruption
- callers can retain `lock_digest` outside the lockfile and supply it during verification for an independent integrity binding that rejects semantic rewrites even when the embedded checksum is recomputed
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

When a lock is written, the JSON response includes `lock_digest`. Preserve that digest in an independent trusted location if you need tamper-evident verification rather than checksum-only corruption detection.

Verify the embedded checksum only:

```bash
model-version-pin --verify-lockfile model-version.lock.json
```

Verify against an independently retained digest:

```bash
model-version-pin \
  --verify-lockfile model-version.lock.json \
  --expected-digest "$TRUSTED_LOCK_DIGEST"
```

The command exits non-zero for refused requests, malformed input, invalid lockfiles, or an external digest mismatch.

## Verify the repository

```bash
python -m pytest -q
python scripts/operate.py
```

`operate.py` exercises the complete local path: production pin validation, deterministic receipt generation, lockfile persistence, lockfile readback, and externally bound integrity verification.

## Threat boundary

A hash stored inside the object it hashes is a **checksum**, not authentication. It can detect accidental corruption, but a party able to rewrite the lockfile can also recompute that embedded checksum. For tamper evidence, retain `lock_digest` through an independent trusted channel, immutable manifest, release metadata store, transparency log, or signature system and pass that value back as `expected_digest` during verification.

## Provider boundary

This repository deliberately does not pretend to possess provider production credentials. Provider adapters may populate `aliases` and `known_versions` from real APIs, but the invariant enforced here is independent of any one hosted-model vendor: **production execution identity must resolve to an immutable version and remain reproducible afterward.**
