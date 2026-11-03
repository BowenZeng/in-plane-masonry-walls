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
    """(accepted, reason) of the squat-wall biaxial method (I-40)."""
    record = SQUAT_BIAXIAL_METHOD_ACCEPTANCE if record is None else record
    if not record:
        return False, "squat-wall biaxial method outstanding (WP-E2d, register I-40): no accepted method"
    missing = [key for key in SQUAT_RECORD_KEYS if not record.get(key)]
    if missing:
        return False, "the squat-wall method acceptance record is incomplete: " + ", ".join(missing)
    stale = []
    for name, digest in record["evidence"].items():
        try:
            if file_sha256(name) != digest:
                stale.append(name)
        except OSError:
            stale.append(name)
    if stale:
        return False, "acceptance evidence stale: " + ", ".join(stale)
    return True, f"{record['method']} (accepted {record['decision_date']} by {record['decided_by']})"


__all__ = ["SQUAT_BIAXIAL_METHOD_ACCEPTANCE", "file_sha256", "squat_method_status"]
