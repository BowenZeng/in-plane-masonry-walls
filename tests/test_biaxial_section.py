"""Biaxial section resistance (plan WP-E2a/E2c; registers I-38, I-40)."""
from __future__ import annotations

from dataclasses import replace
import math
import unittest
from unittest import mock

import acceptance_records
import biaxial_section as bx
import in_plane_engine as ip


def wall(edition="S304:24", **values):
    """The 3000 x 3000 x 190 fully grouted F-077 wall: 8-15M at 400 (e = 100), 15 MPa units, Type S."""
    defaults = dict(edition=edition, length=3000.0, height=3000.0, thickness=190.0, bar_size="15M", bar_count=8,
                    end_bar_distance=100.0, least_clear_cell_dimension=100.0, face_shell_thickness=36.2,
                    minimum_face_shell_thickness=30.0, available_vertical_development=5000.0)
    return ip.WallInput(**(defaults | values))


class UniaxialIdentityTests(unittest.TestCase):
    def test_in_plane_limit_equals_the_engine(self):
        for edition, expected, c in (("S304:24", 782.9985, 700.9), ("S304-14", 748.316, 803.6)):
            base = wall(edition)
            section = bx.build_section(base)
            engine = ip.calculate_wall(replace(base, factored_axial=120.0, factored_moment=640.0,
                                               factored_shear=10.0)).moment_resistance
            limit = bx.uniaxial_limit(section, 120.0, "ip")
            self.assertAlmostEqual(limit / engine, 1.0, delta=1e-9)
            self.assertAlmostEqual(limit, expected, delta=5e-4)
            self.assertAlmostEqual(bx.neutral_axis_at_axial(section, 0.0, 120.0), c, delta=0.05)
            self.assertAlmostEqual(section.axial_cap, {"S304:24": 2093.04, "S304-14": 1744.20}[edition], places=9)

    def test_out_of_plane_limit_of_the_repeating_layout(self):
        """3200 x 190 fully grouted, 8-15M at 400 (e = 200), P 128 kN: the strip's include-grout route x 3.2 m."""
        for edition, expected in (("S304:24", 48.394), ("S304-14", 41.956)):
            section = bx.build_section(wall(edition, length=3200.0, end_bar_distance=200.0))
            self.assertAlmostEqual(bx.uniaxial_limit(section, 128.0, "oop"), expected, delta=2e-3)

    def test_resultants_balance_at_the_solved_depth(self):
        section = bx.build_section(wall())
        for theta in (0.0, 0.3, 1.4, math.pi / 2, 2.5, 4.0):
            c = bx.neutral_axis_at_axial(section, theta, 300.0)
            self.assertAlmostEqual(bx.resultants(section, theta, c)[0], 300.0, delta=1e-6)


class BiaxialTests(unittest.TestCase):
    def test_f077_biaxial_verdicts(self):
        """F-077: (640, 20.25) S304:24 passes, (640, 24.30) S304-14 fails, where the additive screen gave 1.23 / 1.43."""
        for edition, m_oop, utilization, theta in (("S304:24", 20.25, 0.98124, 84.023), ("S304-14", 24.30, 1.12809, 85.960)):
            result = bx.biaxial_resistance(wall(edition), 120.0, 640.0, m_oop, cover_to_bar_centre=95.0)
            self.assertEqual(result.status, "ok")
            self.assertAlmostEqual(1.0 / result.lam, utilization, delta=5e-5)
            self.assertAlmostEqual(math.degrees(result.theta), theta, delta=5e-3)
            self.assertLessEqual(result.residual_direction, 1e-6)
            self.assertLessEqual(result.residual_axial, 1e-6)
            self.assertEqual(result.multiplicity, 1)
            self.assertAlmostEqual(math.hypot(result.mr_ip, result.mr_oop), result.lam * math.hypot(640.0, m_oop),
                                   places=6)

    def test_pure_directions_equal_the_uniaxial_limits(self):
        base = wall()
        for m_ip, m_oop in ((100.0, 0.0), (-150.0, 0.0), (0.0, 20.0), (0.0, -20.0)):
            result = bx.biaxial_resistance(base, 120.0, m_ip, m_oop, cover_to_bar_centre=95.0)
            limit = result.uniaxial_ip if m_oop == 0.0 else result.uniaxial_oop
            self.assertAlmostEqual(result.lam * abs(m_ip or m_oop), limit, places=9)

    def test_mirror_and_reversal_identities(self):
        base = wall()
        a = bx.biaxial_resistance(base, 200.0, 300.0, 15.0, cover_to_bar_centre=70.0)
        b = bx.biaxial_resistance(base, 200.0, 300.0, -15.0, cover_to_bar_centre=120.0)
        self.assertAlmostEqual(a.lam, b.lam, delta=1e-12 * a.lam)
        c = bx.biaxial_resistance(base, 200.0, -300.0, 15.0, cover_to_bar_centre=70.0)
        self.assertAlmostEqual(a.lam, c.lam, delta=1e-9 * a.lam)   # symmetric layout: x -> L - x
        centred = bx.biaxial_resistance(base, 200.0, 300.0, 15.0)
        self.assertAlmostEqual(centred.lam, centred.reversed_lambda, delta=1e-9 * centred.lam)

    def test_statuses(self):
        base = wall()
        self.assertTrue(math.isinf(bx.biaxial_resistance(base, 120.0, 0.0, 0.0).lam))
        self.assertEqual(bx.biaxial_resistance(base, -10.0, 100.0, 0.0).status, "net_tension")
        self.assertEqual(bx.biaxial_resistance(base, 2100.0, 100.0, 0.0).status, "over_capacity")
        squat = bx.biaxial_resistance(wall(height=2000.0), 120.0, 640.0, 20.0, cover_to_bar_centre=95.0)
        self.assertTrue(squat.squat)
        self.assertGreater(squat.tension_bars, 0)


class RegisterAndAcceptanceTests(unittest.TestCase):
    def test_readings_and_squat_gate(self):
        for key in ("I-38", "I-40"):
            self.assertTrue({"question", "reading", "class", "blocking"} <= set(ip.INTERPRETATIONS[key]))
        self.assertIsNone(acceptance_records.SQUAT_BIAXIAL_METHOD_ACCEPTANCE)
        accepted, reason = acceptance_records.squat_method_status()
        self.assertFalse(accepted)
        self.assertIn("WP-E2d", reason)
        record = dict(version="MOCK", method="MOCK - test only", applicability={"hw_over_lw": [0.5, 1.0]},
                      decision_date="2026-11-03", decided_by="MOCK",
                      evidence={n: acceptance_records.file_sha256(n) for n in ("biaxial_section.py", "in_plane_engine.py")})
        with mock.patch.object(acceptance_records, "SQUAT_BIAXIAL_METHOD_ACCEPTANCE", record):
            self.assertFalse(acceptance_records.squat_method_status()[0])
        stale = dict(record, evidence={"biaxial_section.py": "0" * 64})
        self.assertFalse(acceptance_records.squat_method_status(stale)[0])



class IndependentReferenceTests(unittest.TestCase):
    """verify_biaxial_independent.py (plan WP-E2c): a fiber-grid reference with Richardson extrapolation
    and closed-form uniaxial solutions; it imports only the public entry points (run separately, ~2 min)."""

    def test_reference_imports_only_the_public_entry_points(self):
        import ast
        from pathlib import Path

        source = Path(__file__).resolve().parents[1] / "verify_biaxial_independent.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        allowed = {"in_plane_engine": {"WallInput", "calculate_wall"}, "biaxial_section": {"biaxial_resistance"}}
        imported = {name: set() for name in allowed}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.rsplit(".", 1)[-1]
                if module in allowed:
                    imported[module].update(alias.name for alias in node.names)
                self.assertNotIn(module, {"load_combinations", "batch_reporting", "acceptance_records"})
            if isinstance(node, ast.Import):
                self.assertFalse({alias.name.rsplit(".", 1)[-1] for alias in node.names} & set(allowed))
        for module, names in imported.items():
            self.assertTrue(names, module)
            self.assertLessEqual(names, allowed[module], module)


if __name__ == "__main__":
    unittest.main()
