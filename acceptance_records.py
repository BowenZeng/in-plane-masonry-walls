"""Owner acceptance records of gated in-plane capabilities (plan WP-E2d).

Kept apart from the calculation modules so filling a record never changes a
calculation file whose hash it cites. Filled only by the owner in a separate
commit after the evidence exists; tests use mocked records to prove the gate,
never the capability.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Owner decision 2 (3 November 2026), register I-40: squat walls (hw/lw < 1)
# with a bar in tension are UNSUPPORTED in the biaxial section check until a
# method is established (doc/squat_biaxial_method.md, owner-reviewed),
# implemented, independently validated and accepted:
# {"version": "...", "method": "...", "applicability": {...}, "decision_date": "YYYY-MM-DD",
#  "decided_by": "<name>", "evidence": {"biaxial_section.py": "<sha256>",
#  "in_plane_engine.py": "<sha256>", "<reference module>": "<sha256>", "<results summary>": "<sha256>"}}
SQUAT_BIAXIAL_METHOD_ACCEPTANCE: dict | None = None

SQUAT_RECORD_KEYS = ("version", "method", "applicability", "decision_date", "decided_by", "evidence")


def file_sha256(name: str) -> str:
    return sha256((HERE / name).read_bytes()).hexdigest()


def squat_method_status(record: dict | None = None) -> tuple[bool, str]:
    """Option (c), owner decision 4 November 2026: no implemented squat method.

    A populated dictionary cannot implement a calculation or establish its
    applicability. Keep the argument and record symbol for API compatibility.
    """
    return False, ("squat-wall biaxial method outstanding (WP-E2d, register I-40): "
                   "option (c), UNSUPPORTED; no accepted method is implemented")


__all__ = ["SQUAT_BIAXIAL_METHOD_ACCEPTANCE", "file_sha256", "squat_method_status"]
