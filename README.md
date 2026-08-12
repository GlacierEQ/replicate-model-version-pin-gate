# Model Version Pin

Independent GlacierEQ implementation of immutable version binding for hosted model invocations.

> **Not affiliated.** This repository is not affiliated with, endorsed by, employed by, or deployed at Replicate.

## Purpose

Make model invocation identity caller-visible and reproducible. Production requests cannot silently float from one model version to another.

## Behavior

- production requires an explicit 64-hex immutable version identifier
- development/staging aliases are accepted only when an explicit local alias registry resolves them to an immutable version
- optional model/version registries catch pins registered to the wrong model
- parameter and input fingerprints become part of a deterministic invocation key
- request expiry, parameter count, and work budget are enforced
- unknown policy fields fail closed
- every allowed receipt exposes the resolved immutable version

## Run

```bash
python -m pytest -q
python scripts/operate.py
```

Install with `python -m pip install .`; the JSON CLI is `model-version-pin`.

## Boundary

This is a portable invocation-contract kernel. It does not call Replicate APIs or claim Replicate production access. A live adapter can populate alias/version registries from any hosted-model provider.
