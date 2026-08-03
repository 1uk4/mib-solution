#!/usr/bin/env python3
"""L7 policy context — the value objects every stage operates on.

DOES: define Verdict (the L6/L7 result triple), PolicyContext (the frozen
per-case view a stage receives), and make_ctx (test-construction factory).
DOES NOT: contain any policy logic — stages live in upgrades/bypasses/
guards; sequencing lives in v4/policy/__init__.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from v4.config import Config, CONFIG


class Verdict(NamedTuple):
    """(adjudication, confidence, rule_tag) — unpacks as a plain 3-tuple,
    so `adj, conf, tag = apply_policy(...)` call sites are unaffected."""
    adj: str
    conf: float
    tag: str


@dataclass(frozen=True)
class PolicyContext:
    """Everything a policy stage may read. Frozen: stages never mutate
    state — a stage either returns a Verdict (fires) or None (passes).

    Key-access contract preserved from the v3 function body:
      - upgrades read signals via .get() (tolerant of missing keys)
      - guard_ocr_risk_override reads signals["image_ocr"] (KeyError on
        absence — deliberate; the bundle always carries it in production)
    """
    fields: dict
    adj: str
    conf: float
    tag: str
    signals: dict
    config: Config = CONFIG

    @property
    def verdict(self) -> Verdict:
        """The unmodified incoming verdict (used by pass-through exits)."""
        return Verdict(self.adj, self.conf, self.tag)


def make_ctx(
    fields: dict | None = None,
    adj: str = "APPROVED",
    conf: float = 0.69,
    tag: str = "R_A1_paid_clean",
    signals: dict | None = None,
    config: Config = CONFIG,
) -> PolicyContext:
    """One-line PolicyContext construction for per-stage tests."""
    return PolicyContext(
        fields=fields if fields is not None else {},
        adj=adj, conf=conf, tag=tag,
        signals=signals if signals is not None else {
            "combined_text": "", "image_ocr": [],
            "any_illegibility_excluded": False,
        },
        config=config,
    )
