# Issue contract — Model Version Pin Gate

## Problem
Hosted model invocations drift versions without caller-visible pins.

## Desired outcome
A bounded, open, testable implementation of **Model Version Pin Gate** that demonstrates Require explicit model version pins; reject floating tags for production-class calls.

## Non-goals
- Replicate affiliation or proprietary integration
- Portfolio-wide scale/performance claims
- UI marketing site

## Acceptance
1. Mechanism module implements allow + refuse with structured receipts
2. pytest behavioral suite green
3. operate.py cold-start produces JSON receipt
4. Non-affiliation disclaimer preserved
