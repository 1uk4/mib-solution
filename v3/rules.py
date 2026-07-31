#!/usr/bin/env python3
"""L6 — Rule engine.

V1's proven rule chain wrapped as an L6 pass-through. Rules operate on
consolidated field values, not raw sources — they never need to know
where a value came from or whether the source was trusted (L3 already
guaranteed that).

This module deliberately stays a thin wrapper. Rules stay in v1/solution.py
so we don't fork the ruleset — any refinement lands in one place.
"""
from __future__ import annotations

from v1.solution import adjudicate


def apply_rules(fields: dict) -> tuple[str, float, str]:
    """Return (adjudication, confidence, rule_tag) for a consolidated field dict."""
    return adjudicate(fields)
