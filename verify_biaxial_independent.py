"""Independent verification of the biaxial (P, M_ip, M_oop) section solver.

Production imports are limited to in_plane_engine.WallInput / calculate_wall
(calculate_wall only to read the engine's in-plane Mr for the uniaxial
identity) and biaxial_section.biaxial_resistance (the object under test).
The material table, section geometry, stress block, steel law, equilibrium
root and neutral-axis search are re-implemented here from the model
statement, with a different integration method from production:

* f'm: CSA Table 4 (concrete block) rows read from the supplied editions:
  S304:24 from review/CSA-S304-24-extracted.txt (PDF page 147, "Table 4"),
  S304-14 from `pdftotext -layout` of doc/CSA_S304_14_Design of masonry
  structures.pdf ("Table 4"). Linear interpolation in unit strength between
  rows (Table 4 footnote "*Linear interpolation may be used"); 30 MPa or more
  takes the 30 MPa row.
* Section (mm): x along the length 0..L, y through the thickness 0..t. Fully
  grouted: [0, L] x [0, t] at f'm,g. Partially grouted: grouted cells of the
  declared width centred on the bars (clipped to [0, L]) full depth at f'm,g,
  the rest two face shells [0, tf] and [t - tf, t] at f'm,ug, tf the declared
  mortar-bedded shell (S304-14) or the Clause 3.1 minimum shell (S304:24).
* Neutral axis normal n = (cos theta, sin theta), depth u = n.p - min over the
  gross corners; masonry k carries 0.85 phi_m f'm,k where u <= beta_1,k c; no
  masonry tension; steel strain 0.003 (u - c) / c, tension force
  phi_s A min(kdev fy, Es eps), untied bars in compression carry nothing.
  Forces compression positive, moments about (x_c, t/2): M_ip = sum F (x_c - x),
  M_oop = sum F (t/2 - y).
* Integration: a FIBER GRID of cells of size <= h tiling each material
  rectangle exactly, each cell's force acting at its centre, summed strip by
  strip (a row of cells has centres in arithmetic progression, so the sums
  are closed form) - no polygon clipping. The plain cell-centre rule (cell
  active when its centre has u <= beta_1 c) makes P(c) a step function; when
  the neutral axis is parallel to the cell rows (theta = 0, +-pi/2 and
  nearby, i.e. every pure and near-pure out-of-plane case) whole rows switch
  together, c is quantised to the row centres and the error is an O(h)
  saw-tooth that Richardson extrapolation cannot remove (0.4 % at h = 0.5 mm
  for the pure out-of-plane F-077 wall; see the convergence report, which
  keeps that rule for comparison). The reference therefore activates each
  cell linearly across its projected depth w = hx|cos| + hy|sin| along n,
  fraction clip((beta_1 c - u_centre) / w + 1/2, 0, 1): exact area for rows
  parallel to the axis, continuous P(c), and the cell-centre rule as h -> 0.
  Its observed error is O(h^2) (ratio ~4 per halving), so the reference
  lambda is the Richardson extrapolation of order 2 from h = 1 and 0.5 mm;
  h = 4, 2, 1, 0.5 mm are all computed and reported (with the order-1 value).
  The two uniaxial planes use closed-form rectangular blocks (no grid), with
  c by bisection to machine precision. Fiber depth c: safeguarded Illinois
  (monotone axial force); angle theta: a scan over [-pi, pi) (uniform 1 deg
  plus 0.1 deg within 7 deg of +-pi/2) on a coarse grid, then safeguarded
  Illinois on every bracketed root at each h; lambda is the smallest over the
  roots with Mr . Mf > 0.

Every reference case uses available_vertical_development = 5000 mm so that
kdev = 1 (the engine's ld is not re-derived here). The single kdev < 1 case is
compared only between production objects (biaxial uniaxial_ip vs engine Mr).
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import sys
import time

import numpy as np

if __package__:
    from .in_plane_engine import WallInput, calculate_wall
    from .biaxial_section import biaxial_resistance
else:
    from in_plane_engine import WallInput, calculate_wall
    from biaxial_section import biaxial_resistance


PHI_M = 0.60
PHI_S = 0.85
ES = 200000.0
EPS_MU = 0.003
BLOCK = 0.85 * PHI_M
BAR_AREAS_REF = {"10M": 100.0, "15M": 200.0, "20M": 300.0, "25M": 500.0}
FULLY = "Fully grouted reinforced masonry"
PARTIAL = "Partially grouted reinforced masonry"

# CSA Table 4, concrete block masonry, f'm in MPa, read from the supplied
# editions. unit strength: (Type S ungrouted, Type S grouted/solid,
#                            Type N ungrouted, Type N grouted/solid)
TABLE_4 = {
    "S304-14": {10: (6.5, 5.0, 6.0, 4.5), 15: (10.0, 7.5, 8.0, 6.0),
                20: (13.0, 10.0, 10.0, 7.5), 30: (17.5, 13.5, 12.0, 9.0)},
    "S304:24": {10: (7.5, 6.0, 7.0, 5.5), 15: (12.0, 9.0, 9.5, 7.0),
                20: (15.5, 12.0, 12.0, 9.0), 30: (20.5, 16.0, 14.0, 10.5)},
}
TABLE_4_SOURCE = {
    "S304-14": "pdftotext -layout doc/CSA_S304_14_Design of masonry structures.pdf, Table 4",
    "S304:24": "review/CSA-S304-24-extracted.txt, Table 4 (PDF page 147)",
}

H_LEVELS = (4.0, 2.0, 1.0, 0.5)
H_SCAN = 20.0
RICHARDSON_ORDER = 2  # observed order of the ramp grid (convergence report)
LAM_TOL = 1.0e-3
IP_TOL = 1.0e-9
OOP_TOL = 1.0e-6
RESIDUAL_LIMIT = 1.0e-6
IDENTITY_TOL = 1.0e-12
SELF_TOL = 1.0e-4


def table_4_strengths(edition, mortar, unit_strength):
    """(f'm,ug, f'm,g) in MPa, linear in unit strength between Table 4 rows."""
    rows = TABLE_4[edition]
    offset = {"Type S": 0, "Type N": 2}[mortar]
    keys = sorted(rows)
    s = float(unit_strength)
    if s < keys[0]:
        raise ValueError(f"unit strength {s} below Table 4")
    if s >= keys[-1]:
        row = rows[keys[-1]]
        return row[offset], row[offset + 1]
    for low, high in zip(keys, keys[1:]):
        if low <= s <= high:
            w = (s - low) / (high - low)
            a, b = rows[low], rows[high]
            return tuple(a[offset + i] + w * (b[offset + i] - a[offset + i]) for i in (0, 1))
    raise AssertionError


def beta_1_ref(fm):
    return 0.8 if fm <= 20.0 else 0.8 - 0.01 * (fm - 20.0)


class Section:
    """Reference section: material rectangles, bars, centroid, Pr,max."""

    def __init__(self, wall, cover=None, kdev=1.0):
        if wall.wall_type not in (FULLY, PARTIAL):
            raise ValueError("reinforced wall types only")
        self.L = L = float(wall.length)
        self.t = t = float(wall.thickness)
        self.f_ug, self.f_g = table_4_strengths(wall.edition, wall.mortar_type, wall.nominal_unit_strength)
        n, e = wall.bar_count, float(wall.end_bar_distance)
        xs = [L / 2] if n == 1 else [e + i * (L - 2 * e) / (n - 1) for i in range(n)]
        self.cover = t / 2 if cover is None else float(cover)
        y_bar = t - self.cover
        area = BAR_AREAS_REF[wall.bar_size]
        self.xb = np.array(xs, dtype=float)
        self.yb = np.full(len(xs), y_bar)
        self.ab = np.full(len(xs), area)
        regions = []
        if wall.wall_type == PARTIAL:
            tf = float(wall.minimum_face_shell_thickness if wall.edition == "S304:24" else wall.face_shell_thickness)
            self.tf = tf
            w = float(wall.grouted_cell_width)
            cells = sorted((max(0.0, x - w / 2), min(L, x + w / 2)) for x in xs)
            merged = []
            for a, b in cells:
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            cursor = 0.0
            for a, b in merged:
                if a > cursor:
                    regions += [(cursor, a, 0.0, tf, self.f_ug), (cursor, a, t - tf, t, self.f_ug)]
                regions.append((a, b, 0.0, t, self.f_g))
                cursor = b
            if cursor < L:
                regions += [(cursor, L, 0.0, tf, self.f_ug), (cursor, L, t - tf, t, self.f_ug)]
        else:
            self.tf = None
            regions = [(0.0, L, 0.0, t, self.f_g)]
        self.regions = regions
        areas = [(x1 - x0) * (y1 - y0) for x0, x1, y0, y1, _ in regions]
        self.area = sum(areas)
        self.x_c = sum(a * (r[0] + r[1]) / 2 for a, r in zip(areas, regions)) / self.area
        self.y_c = t / 2
        self.p_full = sum(BLOCK * r[4] * a for a, r in zip(areas, regions))  # N
        self.axial_cap = 0.8 * self.p_full / 1000.0
        self.beta_min = min(beta_1_ref(r[4]) for r in regions)
        self.kdev, self.fy = float(kdev), float(wall.fy)

    def steel(self, u, c):
        eps = EPS_MU * (u - c) / c
        stress = np.where(eps > 0.0, np.minimum(self.kdev * self.fy, ES * eps), 0.0)
        return PHI_S * self.ab * stress

    # ---- closed-form rectangular blocks for theta = 0, pi, pi/2, -pi/2 ----
    def axis_resultant(self, axis, c):
        index, sense = axis
        extent = self.L if index == 0 else self.t
        P = mip = moop = 0.0
        for x0, x1, y0, y1, f in self.regions:
            a = beta_1_ref(f) * c
            lo, hi = (x0, x1) if index == 0 else (y0, y1)
            lo, hi = (lo, min(hi, a)) if sense > 0 else (max(lo, extent - a), hi)
            if hi <= lo:
                continue
            bx0, bx1, by0, by1 = (lo, hi, y0, y1) if index == 0 else (x0, x1, lo, hi)
            force = BLOCK * f * (bx1 - bx0) * (by1 - by0)
            P += force
            mip += force * (self.x_c - (bx0 + bx1) / 2)
            moop += force * (self.y_c - (by0 + by1) / 2)
        coord = self.xb if index == 0 else self.yb
        u = coord if sense > 0 else extent - coord
        tension = self.steel(u, c)
        P -= tension.sum()
        mip -= (tension * (self.x_c - self.xb)).sum()
        moop -= (tension * (self.y_c - self.yb)).sum()
        return P / 1e3, mip / 1e6, moop / 1e6

    def axis_solution(self, axis, p):
        extent = self.L if axis[0] == 0 else self.t
        lo, hi = 0.0, extent / self.beta_min * 1.001 + 1.0
        for _ in range(400):
            mid = 0.5 * (lo + hi)
            if not lo < mid < hi:
                break
            if self.axis_resultant(axis, mid)[0] < p:
                lo = mid
            else:
                hi = mid
        best = hi
        if lo > 0.0 and abs(self.axis_resultant(axis, lo)[0] - p) < abs(self.axis_resultant(axis, hi)[0] - p):
            best = lo
        P, mip, moop = self.axis_resultant(axis, best)
        return {"c": best, "p": P, "mip": mip, "moop": moop}

    def uniaxial_ip(self, p, sense):
        return abs(self.axis_solution((0, 1 if sense >= 0 else -1), p)["mip"])

    def uniaxial_oop(self, p, sense):
        return abs(self.axis_solution((1, 1 if sense >= 0 else -1), p)["moop"])


class FiberGrid:
    """Fiber grid over the material rectangles, evaluated per strip of cells.

    Cells of size <= h tile each rectangle exactly (nx = ceil(w / h) cells of
    w / nx, likewise through the depth). The cells of one row (or column) form
    a strip whose centres lie on an arithmetic progression, so the strip sums
    are formed in closed form instead of cell by cell.

    ramp=False is the plain cell-centre rule (a cell carries its full force
    when its centre has u <= beta_1 c). P(c) is then a step function; at the
    root the cells that switch are activated fractionally. When the neutral
    axis is parallel to the cell rows (theta = 0 or +-pi/2, and nearly so)
    whole rows switch together and the depth c is quantised to the row
    centres, which leaves an O(h) saw-tooth error that Richardson
    extrapolation cannot remove (see the convergence report).

    ramp=True (the reference) activates each cell linearly across its
    projected depth along n, w = hx |cos| + hy |sin|: fraction
    clip((beta_1 c - u_centre) / w + 1/2, 0, 1). The cell force stays at the
    cell centre (no clipping of cells against the neutral axis). P(c) becomes
    continuous, the activation is exact for rows parallel to the axis, and the
    rule tends to the cell-centre rule as h -> 0.
    """

    def __init__(self, section, h, ramp=True):
        self.s = section
        self.h = h
        self.ramp = ramp
        columns = {name: [] for name in ("px", "py", "dx", "dy", "hs", "ns", "force", "beta", "cx", "cy")}

        def add(**values):
            for name, value in values.items():
                columns[name].append(value)

        for x0, x1, y0, y1, f in section.regions:
            nx = max(1, math.ceil((x1 - x0) / h - 1e-9))
            ny = max(1, math.ceil((y1 - y0) / h - 1e-9))
            hx, hy = (x1 - x0) / nx, (y1 - y0) / ny
            cell = BLOCK * f * hx * hy
            if nx >= ny:  # one strip along x per row of cells
                for j in range(ny):
                    add(px=x0 + hx / 2, py=y0 + (j + 0.5) * hy, dx=1.0, dy=0.0, hs=hx, ns=nx,
                        force=cell, beta=beta_1_ref(f), cx=hx, cy=hy)
            else:  # one strip along y per column of cells
                for i in range(nx):
                    add(px=x0 + (i + 0.5) * hx, py=y0 + hy / 2, dx=0.0, dy=1.0, hs=hy, ns=ny,
                        force=cell, beta=beta_1_ref(f), cx=hx, cy=hy)
        for name, values in columns.items():
            setattr(self, name, np.array(values, dtype=float)[None, :])
        self.cells = int(np.sum(self.ns))
        self.strips = self.px.shape[1]

    def _strip_sums(self, cos, sin, c):
        """Per strip: S0 = sum of cell activations, S1 = sum of k * activation
        (k = cell index along the strip)."""
        s = self.s
        n = self.ns
        umin = np.minimum(0.0, s.L * cos) + np.minimum(0.0, s.t * sin)
        # u of cell k = u0 + k * step; active where u <= beta_1 c
        room = self.beta * c + umin - (self.px * cos + self.py * sin)
        step = self.hs * (self.dx * cos + self.dy * sin)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            if not self.ramp:
                q = room / step
                first = np.clip(np.floor(q) + 1.0, 0.0, n)
                kmin_neg = np.clip(np.ceil(q), 0.0, n)
                count = np.where(step > 0.0, first, np.where(step < 0.0, n - kmin_neg,
                                                             np.where(room >= 0.0, n, 0.0)))
                kmin = np.where(step < 0.0, kmin_neg, 0.0)
                return count, count * kmin + 0.5 * count * (count - 1.0), umin
            # activation of cell k: clip(G - B k, 0, 1)
            width = self.cx * np.abs(cos) + self.cy * np.abs(sin)
            G = room / width + 0.5
            B = step / width
            reverse = B < 0.0  # index from the far end so that the slope is positive
            Gp = np.where(reverse, G - B * (n - 1.0), G)
            Bp = np.abs(B)
            Bs = np.where(Bp > 0.0, Bp, 1.0)
            full = np.clip(np.floor((Gp - 1.0) / Bs) + 1.0, 0.0, n)
            positive = np.clip(np.ceil(Gp / Bs), 0.0, n)
            positive = np.maximum(positive, full)
            m = positive - full
            k1 = m * full + 0.5 * m * (m - 1.0)
            k2 = _square_sum(positive) - _square_sum(full)
            S0 = full + m * Gp - Bp * k1
            S1 = 0.5 * full * (full - 1.0) + Gp * k1 - Bp * k2
            flat = np.clip(Gp, 0.0, 1.0)
            S0 = np.where(Bp > 0.0, S0, n * flat)
            S1 = np.where(Bp > 0.0, S1, flat * 0.5 * n * (n - 1.0))
            S1 = np.where(reverse, (n - 1.0) * S0 - S1, S1)
        return S0, S1, umin

    def axial(self, cos, sin, c):
        S0, _, umin = self._strip_sums(cos, sin, c)
        P = (S0 * self.force).sum(axis=1)
        u = self.s.xb * cos + self.s.yb * sin - umin
        return (P - self.s.steel(u, c).sum(axis=1)) / 1e3

    def resultant(self, cos, sin, c):
        s = self.s
        S0, S1, umin = self._strip_sums(cos, sin, c)
        sum_x = S0 * self.px + self.hs * self.dx * S1
        sum_y = S0 * self.py + self.hs * self.dy * S1
        P = (S0 * self.force).sum(axis=1)
        mip = (self.force * (S0 * s.x_c - sum_x)).sum(axis=1)
        moop = (self.force * (S0 * s.y_c - sum_y)).sum(axis=1)
        u = s.xb * cos + s.yb * sin - umin
        tension = s.steel(u, c)
        P = P - tension.sum(axis=1)
        mip = mip - (tension * (s.x_c - s.xb)).sum(axis=1)
        moop = moop - (tension * (s.y_c - s.yb)).sum(axis=1)
        return P / 1e3, mip / 1e6, moop / 1e6

    def c_upper(self, cos, sin):
        return (np.abs(self.s.L * cos) + np.abs(self.s.t * sin)) / self.s.beta_min * 1.001 + 1.0

    def solve(self, thetas, p, bits=32):
        """Vectorised equilibrium for many angles (the scan): bisection on c
        (P is non-decreasing in c), linear blend of the final bracket."""
        th = np.atleast_1d(np.asarray(thetas, dtype=float))
        cos, sin = np.cos(th)[:, None], np.sin(th)[:, None]
        lo = np.zeros_like(th)
        hi = self.c_upper(cos[:, 0], sin[:, 0])
        for _ in range(bits):
            mid = 0.5 * (lo + hi)
            below = self.axial(cos, sin, mid[:, None]) < p
            lo = np.where(below, mid, lo)
            hi = np.where(below, hi, mid)
        lo = np.maximum(lo, hi * 2.0 ** -60)
        p_lo, mip_lo, moop_lo = self.resultant(cos, sin, lo[:, None])
        p_hi, mip_hi, moop_hi = self.resultant(cos, sin, hi[:, None])
        jump = p_hi - p_lo
        with np.errstate(divide="ignore", invalid="ignore"):
            alpha = np.where(jump > 0.0, np.clip((p - p_lo) / jump, 0.0, 1.0), 1.0)
        return {"c": lo + alpha * (hi - lo), "mip": mip_lo + alpha * (mip_hi - mip_lo),
                "moop": moop_lo + alpha * (moop_hi - moop_lo)}

    def point(self, theta, p):
        """Equilibrium at one angle: safeguarded Illinois on c, then a linear
        blend of the final bracket (fractional activation of a step in the
        cell-centre mode). Returns (M_ip, M_oop, c, axial residual kN)."""
        cos, sin = np.array([[math.cos(theta)]]), np.array([[math.sin(theta)]])
        scale = self.s.p_full / 1e3

        def evaluate(c):
            P, mip, moop = self.resultant(cos, sin, np.array([[c]]))
            return (float(P[0]) - p) / scale, (float(mip[0]), float(moop[0]))

        hi = float(self.c_upper(cos, sin)[0, 0])
        ends = refine_root(evaluate, hi * 1e-12, hi, xtol=hi * 1e-15, ftol=1e-15)
        if ends is None:
            raise ArithmeticError(f"no equilibrium depth at theta={theta}, P={p}")
        (ca, ga, pa), (cb, gb, pb) = ends
        w = 0.0 if ga == gb else ga / (ga - gb)
        residual = (ga + w * (gb - ga)) * scale
        return pa[0] + w * (pb[0] - pa[0]), pa[1] + w * (pb[1] - pa[1]), ca + w * (cb - ca), residual


def _square_sum(n):
    """sum_{k=0}^{n-1} k^2"""
    return (n - 1.0) * n * (2.0 * n - 1.0) / 6.0


def scan_angles():
    base = -math.pi + (np.arange(360) + 0.37) * (2 * math.pi / 360)
    dense = [centre + (np.arange(-70, 70) + 0.41) * math.radians(0.1) for centre in (math.pi / 2, -math.pi / 2)]
    return np.unique(np.concatenate([base] + dense))


def richardson(coarse, fine, order=RICHARDSON_ORDER):
    """Extrapolate f(h) = f0 + C h^order from f(2h) = coarse and f(h) = fine."""
    return fine + (fine - coarse) / (2.0 ** order - 1.0)


def wrap(theta):
    return (theta + math.pi) % (2 * math.pi) - math.pi


def refine_root(evaluate, a, b, xtol=1e-10, ftol=0.0, max_iterations=300):
    """Safeguarded Illinois (regula falsi with halving of a retained end, and a
    bisection step whenever two steps fail to halve the bracket) on a sign-change
    bracket; evaluate(x) -> (value, payload). Returns the end points
    (x, value, payload) of the final bracket, or None without a sign change."""
    da, pa = evaluate(a)
    db, pb = evaluate(b)
    if abs(da) <= ftol:
        return (a, da, pa), (a, da, pa)
    if abs(db) <= ftol:
        return (b, db, pb), (b, db, pb)
    if da * db > 0.0:
        return None
    fa, fb = da, db  # Illinois-modified values
    side = 0
    widths = [abs(b - a)]
    for _ in range(max_iterations):
        if abs(b - a) <= xtol:
            break
        stalled = len(widths) >= 3 and widths[-1] > 0.5 * widths[-3]
        x = 0.5 * (a + b) if stalled else (a * fb - b * fa) / (fb - fa)
        if not min(a, b) < x < max(a, b):
            x = 0.5 * (a + b)
            if not min(a, b) < x < max(a, b):
                break
        dx, px = evaluate(x)
        if abs(dx) <= ftol:
            return (x, dx, px), (x, dx, px)
        if dx * db > 0.0:
            b, db, pb, fb = x, dx, px, dx
            if side == -1:
                fa *= 0.5
            side = -1
        else:
            a, da, pa, fa = x, dx, px, dx
            if side == 1:
                fb *= 0.5
            side = 1
        widths.append(abs(b - a))
    return (a, da, pa), (b, db, pb)


class Reference:
    """Caches sections, fiber grids, angle scans and reference solutions."""

    def __init__(self, walls):
        self.walls = walls
        self._sections, self._grids, self._scans, self._lams = {}, {}, {}, {}

    def section(self, wall_id, cover=None):
        key = (wall_id, cover)
        if key not in self._sections:
            self._sections[key] = Section(self.walls[wall_id], cover)
        return self._sections[key]

    def grid(self, wall_id, cover, h, ramp=True):
        key = (wall_id, cover, h, ramp)
        if key not in self._grids:
            self._grids[key] = FiberGrid(self.section(wall_id, cover), h, ramp)
        return self._grids[key]

    def scan(self, wall_id, cover, p):
        key = (wall_id, cover, p)
        if key not in self._scans:
            th = scan_angles()
            r = self.grid(wall_id, cover, H_SCAN).solve(th, p, bits=26)
            self._scans[key] = (th, r["mip"], r["moop"])
        return self._scans[key]

    def lam_at_h(self, wall_id, cover, p, mf, h, brackets, ramp=True):
        grid = self.grid(wall_id, cover, h, ramp)
        norm = math.hypot(*mf)
        u = (mf[0] / norm, mf[1] / norm)

        def evaluate(theta):  # sine of the angle from Mf to Mr
            mip, moop, c, _ = grid.point(theta, p)
            return (mip * u[1] - moop * u[0]) / math.hypot(mip, moop), (mip, moop, c)

        roots = []
        for a, b in brackets:
            width = b - a
            found = None
            for k in range(10):
                found = refine_root(evaluate, a, b, xtol=1e-12, ftol=1e-15)
                if found is not None:
                    break
                a, b = a - width * 2 ** k, b + width * 2 ** k
            if found is None:
                continue
            (ta, da, pa), (tb, db, pb) = found
            w = 0.0 if da == db else da / (da - db)
            theta = ta + w * (tb - ta)
            mip = pa[0] + w * (pb[0] - pa[0])
            moop = pa[1] + w * (pb[1] - pa[1])
            c = pa[2] + w * (pb[2] - pa[2])
            dot = mip * mf[0] + moop * mf[1]
            if dot <= 0.0:
                continue
            theta = wrap(theta)
            if any(abs(wrap(theta - r["theta"])) < 1e-7 for r in roots):
                continue
            mr = math.hypot(mip, moop)
            roots.append({"theta": theta, "c": c, "mr_ip": mip, "mr_oop": moop, "lam": dot / norm ** 2,
                          "direction_residual": abs(mip * u[1] - moop * u[0]) / mr})
        if not roots:
            return None
        best = min(roots, key=lambda r: r["lam"])
        return dict(best, multiplicity=len(roots), h=h, cells=grid.cells, roots=[r["theta"] for r in roots])

    def brackets(self, wall_id, cover, p, mf):
        th, mip, moop = self.scan(wall_id, cover, p)
        norm = math.hypot(*mf)
        d = (mip * mf[1] - moop * mf[0]) / norm
        dot = mip * mf[0] + moop * mf[1]
        # The coarse grid can give an exactly zero cross product over a run of
        # angles (whole rows switching together); bracket between consecutive
        # non-zero values of opposite sign.
        nonzero = np.flatnonzero(d != 0.0)
        out = []
        for index, i in enumerate(nonzero):
            k = nonzero[(index + 1) % len(nonzero)]
            if d[i] * d[k] >= 0.0 or dot[i] + dot[k] <= 0.0:
                continue
            out.append((float(th[i]), float(th[k] + (2 * math.pi if k <= i else 0.0))))
        return out

    def lam(self, wall_id, cover, p, mf, ramp=True):
        key = (wall_id, cover, p, mf, ramp)
        if key in self._lams:
            return self._lams[key]
        brackets = self.brackets(wall_id, cover, p, mf)
        n_brackets = len(brackets)
        levels = []
        for h in H_LEVELS:  # coarse to fine; each level starts next to the previous roots
            level = self.lam_at_h(wall_id, cover, p, mf, h, brackets, ramp)
            levels.append(level)
            if level is None:
                break
            brackets = [(r - 2e-4, r + 2e-4) for r in level["roots"]]
        if any(level is None for level in levels):
            result = {"lam": None, "levels": levels, "brackets": n_brackets}
        else:
            by_h = {lv["h"]: lv for lv in levels}
            extrapolated = richardson(by_h[1.0]["lam"], by_h[0.5]["lam"])
            theta_x = richardson(by_h[1.0]["theta"], by_h[0.5]["theta"])
            c_x = richardson(by_h[1.0]["c"], by_h[0.5]["c"])
            result = {"lam": extrapolated, "theta": theta_x, "c": c_x,
                      "lam_order_1": richardson(by_h[1.0]["lam"], by_h[0.5]["lam"], 1),
                      "lam_h05": by_h[0.5]["lam"],
                      "multiplicity": by_h[0.5]["multiplicity"], "brackets": n_brackets,
                      "levels": [{k: lv[k] for k in ("h", "lam", "theta", "c", "multiplicity",
                                                     "direction_residual", "cells")} for lv in levels]}
        self._lams[key] = result
        return result


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def make_wall(edition, length, height, wall_type, end_distance, **extra):
    data = dict(edition=edition, wall_type=wall_type, length=float(length), height=float(height),
                thickness=190.0, nominal_unit_strength=15.0, mortar_type="Type S",
                face_shell_thickness=36.2, minimum_face_shell_thickness=30.0,
                least_clear_cell_dimension=100.0, grouted_cell_width=200.0,
                bar_size="15M", bar_count=8, end_bar_distance=float(end_distance), fy=400.0,
                available_vertical_development=5000.0, compression_reinforcement_tied=False)
    data.update(extra)
    return WallInput(**data)


WALLS = {
    "F077-24": make_wall("S304:24", 3000, 3000, FULLY, 100),
    "F077-14": make_wall("S304-14", 3000, 3000, FULLY, 100),
    "L3200-FG-24": make_wall("S304:24", 3200, 3000, FULLY, 200),
    "L3200-FG-14": make_wall("S304-14", 3200, 3000, FULLY, 200),
    "L3200-PG-24": make_wall("S304:24", 3200, 3000, PARTIAL, 200),
    "L3200-PG-14": make_wall("S304-14", 3200, 3000, PARTIAL, 200),
}
F077_MOMENTS = {"F077-24": (640.0, 20.25), "F077-14": (640.0, 24.30)}
F077_PLANNING = {"F077-24": 0.98, "F077-14": 1.13}


def moment_pair(degrees, magnitude=100.0):
    exact = {0: (1.0, 0.0), 90: (0.0, 1.0), -90: (0.0, -1.0), 180: (-1.0, 0.0)}
    if degrees in exact:
        e = exact[degrees]
        return (magnitude * e[0], magnitude * e[1])
    r = math.radians(degrees)
    return (magnitude * math.cos(r), magnitude * math.sin(r))


def rel_error(actual, expected):
    if actual == expected:
        return 0.0
    return abs(actual - expected) / max(abs(expected), 1e-300)


def clean(value):
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    return value


class Validation:
    def __init__(self):
        self.ref = Reference(WALLS)
        self.comparisons = []
        self.rows = []
        self._production = {}
        self._engine = {}

    # -- recording --
    def _record(self, group, case, quantity, actual, expected, tolerance, kind, error, passed, **info):
        self.comparisons.append(clean(dict(group=group, case=case, quantity=quantity, production=actual,
                                           reference=expected, tolerance=tolerance, kind=kind,
                                           error=error, passed=bool(passed), **info)))
        return passed

    def rel(self, group, case, quantity, actual, expected, tolerance, **info):
        if actual is None or expected is None or not math.isfinite(actual) or not math.isfinite(expected):
            return self._record(group, case, quantity, actual, expected, tolerance, "relative", None, False, **info)
        error = rel_error(actual, expected)
        return self._record(group, case, quantity, actual, expected, tolerance, "relative", error,
                            error <= tolerance, **info)

    def at_most(self, group, case, quantity, actual, limit):
        passed = actual is not None and math.isfinite(actual) and actual <= limit
        return self._record(group, case, quantity, actual, limit, limit, "at_most", actual, passed)

    def equal(self, group, case, quantity, actual, expected):
        return self._record(group, case, quantity, actual, expected, None, "equal", None, actual == expected)

    # -- production / engine --
    def production(self, wall_id, p, m_ip, m_oop, cover=None, samples=72, kdev_wall=None):
        key = (wall_id, kdev_wall is not None, p, m_ip, m_oop, cover, samples)
        if key not in self._production:
            wall = kdev_wall if kdev_wall is not None else WALLS[wall_id]
            self._production[key] = biaxial_resistance(wall, p, m_ip, m_oop, cover_to_bar_centre=cover,
                                                       samples=samples)
        return self._production[key]

    def engine_mr(self, wall, p):
        key = (id(wall), p)
        if key not in self._engine:
            self._engine[key] = calculate_wall(replace(wall, factored_axial=p, factored_moment=100.0,
                                                       factored_shear=50.0)).moment_resistance
        return self._engine[key]

    # -- one biaxial case --
    def biaxial_case(self, group, wall_id, p, mf, cover=None, label=None):
        wall = WALLS[wall_id]
        sec = self.ref.section(wall_id, cover)
        case = label or f"{wall_id} cover={'t/2' if cover is None else cover} P={p:.3f} M=({mf[0]:.4f},{mf[1]:.4f})"
        res = self.production(wall_id, p, mf[0], mf[1], cover)
        ref = self.ref.lam(wall_id, cover, p, mf)
        self.equal(group, case, "status", res.status, "ok")
        self.rel(group, case, "lambda", res.lam, ref["lam"], LAM_TOL,
                 fiber_levels=ref.get("levels"), reference_theta=ref.get("theta"), production_theta=res.theta,
                 reference_c=ref.get("c"), production_c=res.c,
                 reference_multiplicity=ref.get("multiplicity"), production_multiplicity=res.multiplicity)
        self.at_most(group, case, "residual_direction", res.residual_direction, RESIDUAL_LIMIT)
        self.at_most(group, case, "residual_axial", res.residual_axial, RESIDUAL_LIMIT)
        if mf[1] != 0.0:
            y_symmetric = abs(sec.cover - sec.t / 2) < 1e-12
            ref_rev = ref if y_symmetric else self.ref.lam(wall_id, cover, p, (mf[0], -mf[1]))
            self.rel(group, case, "reversed_lambda", res.reversed_lambda, ref_rev["lam"], LAM_TOL)
        self.uniaxial_checks(group, case, wall_id, wall, sec, p, mf, res)
        self.rel(group, case, "axial_cap", res.axial_cap, sec.axial_cap, IP_TOL)
        theta_diff = None
        if res.theta is not None and ref.get("theta") is not None:
            theta_diff = abs(wrap(res.theta - ref["theta"]))
        self.rows.append(dict(group=group, case=case, wall=wall_id, cover=cover, p=p, m_ip=mf[0], m_oop=mf[1],
                              production_lam=res.lam, reference_lam=ref["lam"],
                              lam_rel_error=None if res.lam is None or ref["lam"] is None else rel_error(res.lam, ref["lam"]),
                              theta_difference=theta_diff,
                              c_difference=None if res.c is None or ref.get("c") is None else abs(res.c - ref["c"])))
        return res, ref

    def uniaxial_checks(self, group, case, wall_id, wall, sec, p, mf, res):
        ip_ref = sec.uniaxial_ip(p, mf[0])
        oop_ref = sec.uniaxial_oop(p, mf[1])
        self.rel(group, case, "uniaxial_ip vs closed form", res.uniaxial_ip, ip_ref, IP_TOL)
        if wall.height / wall.length >= 1.0:
            engine = self.engine_mr(wall, p)
            self.rel(group, case, "uniaxial_ip vs engine Mr", res.uniaxial_ip, engine, IP_TOL)
            self.rel(group, case, "closed form vs engine Mr (reference)", ip_ref, engine, IP_TOL)
        self.rel(group, case, "uniaxial_oop vs closed form", res.uniaxial_oop, oop_ref, OOP_TOL)

    # -- groups --
    def run_f077_matrix(self):
        for wall_id in ("F077-24", "F077-14"):
            cap = self.ref.section(wall_id).axial_cap
            for p in (0.0, 120.0, 0.3 * cap, 0.6 * cap):
                for degrees in (0, 1.8, 10, 45, 80, 90):
                    self.biaxial_case("F077 matrix", wall_id, p, moment_pair(degrees))

    def run_f077_case(self):
        out = {}
        for wall_id, mf in F077_MOMENTS.items():
            res, ref = self.biaxial_case("F077 case", wall_id, 120.0, mf, label=f"{wall_id} F-077 P=120 M={mf}")
            out[wall_id] = {"edition": WALLS[wall_id].edition, "p": 120.0, "m_ip": mf[0], "m_oop": mf[1],
                            "production_lam": res.lam, "production_inverse": 1.0 / res.lam,
                            "reference_lam": ref["lam"], "reference_inverse": 1.0 / ref["lam"],
                            "planning_inverse": F077_PLANNING[wall_id],
                            "production_theta": res.theta, "reference_theta": ref["theta"],
                            "production_c": res.c, "reference_c": ref["c"]}
        return out

    def run_layout_matrix(self):
        for wall_id in ("L3200-FG-24", "L3200-FG-14", "L3200-PG-24", "L3200-PG-14"):
            cap = self.ref.section(wall_id).axial_cap
            for p in (0.0, 120.0, 0.3 * cap):
                for degrees in (0, 10, 45, 80, 90):
                    self.biaxial_case("3200 layout", wall_id, p, moment_pair(degrees))
                for degrees in (0, 10, 45, 90, -10, -45, -90):
                    self.biaxial_case("3200 layout cover 70", wall_id, p, moment_pair(degrees), cover=70.0)

    def run_identities(self):
        group = "identity (production only)"
        for wall_id in ("L3200-FG-24", "L3200-FG-14", "L3200-PG-24", "L3200-PG-14", "F077-24"):
            for degrees in (10, 45, 80):
                mf = moment_pair(degrees)
                a = self.production(wall_id, 120.0, mf[0], mf[1], 70.0)
                b = self.production(wall_id, 120.0, mf[0], -mf[1], 120.0)
                case = f"{wall_id} P=120 dir={degrees}: cover 70 +M_oop vs cover 120 -M_oop"
                self.rel(group, case, "lambda", a.lam, b.lam, IDENTITY_TOL)
                self.rel(group, case, "reversed_lambda", a.reversed_lambda, b.reversed_lambda, IDENTITY_TOL)
                self.rel(group, case, "uniaxial_oop", a.uniaxial_oop, b.uniaxial_oop, IDENTITY_TOL)
                c = self.production(wall_id, 120.0, mf[0], -mf[1], 70.0)
                self.rel(group, f"{wall_id} P=120 dir={degrees} cover 70: reversed_lambda vs -M_oop call",
                         "reversed_lambda", a.reversed_lambda, c.lam, IDENTITY_TOL)
        for wall_id in ("F077-24", "F077-14", "L3200-FG-24", "L3200-PG-14"):
            for degrees in (1.8, 10, 45, 80):
                for cover in (None, 70.0):
                    mf = moment_pair(degrees)
                    a = self.production(wall_id, 120.0, mf[0], mf[1], cover)
                    b = self.production(wall_id, 120.0, -mf[0], mf[1], cover)
                    case = f"{wall_id} P=120 dir={degrees} cover={'t/2' if cover is None else cover}: x -> L - x, -M_ip"
                    self.rel(group, case, "lambda", b.lam, a.lam, IDENTITY_TOL)

    def run_status_cases(self):
        group = "status / axial"
        anchors = {"F077-24": 2093.04, "F077-14": 1744.20}
        for wall_id, anchor in anchors.items():
            sec = self.ref.section(wall_id)
            res = self.production(wall_id, 120.0, 0.0, 0.0)
            case = f"{wall_id} P=120, M=(0,0)"
            self.equal(group, case, "lambda is +inf", res.lam is not None and math.isinf(res.lam) and res.lam > 0, True)
            self.rel(group, case, "axial_cap vs anchor", res.axial_cap, anchor, IP_TOL)
            self.rel(group, case, "reference Pr,max vs anchor", sec.axial_cap, anchor, IP_TOL)
        for wall_id in WALLS:
            cap = self.ref.section(wall_id).axial_cap
            self.equal(group, f"{wall_id} P=-25 kN", "status", self.production(wall_id, -25.0, 300.0, 10.0).status,
                       "net_tension")
            self.equal(group, f"{wall_id} P=1.001 Pr,max", "status",
                       self.production(wall_id, 1.001 * cap, 300.0, 10.0).status, "over_capacity")
            self.rel(group, f"{wall_id} Pr,max", "axial_cap", self.production(wall_id, 120.0, 300.0, 10.0).axial_cap,
                     cap, IP_TOL)

    def run_self_checks(self):
        """Reference self-consistency: extrapolated fiber vs closed-form blocks."""
        group = "self (fiber vs closed form)"
        for wall_id in ("F077-24", "L3200-PG-14"):
            sec = self.ref.section(wall_id)
            for p in (0.0, 120.0, 0.3 * sec.axial_cap):
                for mf, closed in (((100.0, 0.0), sec.uniaxial_ip(p, 1)), ((0.0, 100.0), sec.uniaxial_oop(p, 1))):
                    ref = self.ref.lam(wall_id, None, p, mf)
                    self.rel(group, f"{wall_id} P={p:.3f} M={mf}", "fiber lam*|Mf| vs closed form",
                             None if ref["lam"] is None else ref["lam"] * 100.0, closed, SELF_TOL)

    def run_kdev_case(self):
        group = "kdev < 1 (production only)"
        base = WALLS["F077-24"]
        short = replace(base, available_vertical_development=250.0)
        sec = self.ref.section("F077-24")
        info = []
        for p in (120.0, 0.3 * sec.axial_cap):
            res = self.production("F077-24-dev250", p, 640.0, 20.25, kdev_wall=short)
            engine = self.engine_mr(short, p)
            self.rel(group, f"F077-24 available development 250 mm, P={p:.3f}", "uniaxial_ip vs engine Mr",
                     res.uniaxial_ip, engine, IP_TOL)
            info.append({"p": p, "production_uniaxial_ip": res.uniaxial_ip, "engine_mr": engine,
                         "kdev_1_closed_form": sec.uniaxial_ip(p, 1), "lam": res.lam})
        return info

    def convergence(self):
        """Fiber h-study (ramp reference and the plain cell-centre rule) and the
        production angular sampling study."""
        pg_cap = self.ref.section("L3200-PG-14", 70.0).axial_cap
        cases = [("F077-24", None, 120.0, F077_MOMENTS["F077-24"], None),
                 ("L3200-PG-14", 70.0, 0.3 * pg_cap, moment_pair(45), None),
                 ("F077-24", None, 120.0, moment_pair(90),
                  self.ref.section("F077-24").uniaxial_oop(120.0, 1) / 100.0)]
        fiber = []
        for wall_id, cover, p, mf, exact in cases:
            item = {"case": f"{wall_id} cover={'t/2' if cover is None else cover} P={p:.3f} M=({mf[0]:.4f},{mf[1]:.4f})",
                    "h": list(H_LEVELS), "closed_form_lam": exact}
            for label, ramp in (("ramp", True), ("cell_centre", False)):
                ref = self.ref.lam(wall_id, cover, p, mf, ramp=ramp)
                lams = [lv["lam"] for lv in ref["levels"]]
                target = ref["lam"] if exact is None else exact
                errors = [lam - target for lam in lams]
                diffs = [lams[i] - lams[i + 1] for i in range(len(lams) - 1)]
                item[label] = {
                    "lam": lams,
                    "extrapolated_order_2": ref["lam"],
                    "extrapolated_order_1": ref["lam_order_1"],
                    "error_vs": "closed form" if exact is not None else "own order-2 extrapolation",
                    "error": errors,
                    "error_ratio_h_over_h2": [errors[i] / errors[i + 1] if errors[i + 1] else None
                                              for i in range(len(errors) - 1)],
                    "observed_order_from_differences": [math.log2(abs(diffs[i] / diffs[i + 1])) if diffs[i + 1] else None
                                                        for i in range(len(diffs) - 1)],
                    "cells": [lv["cells"] for lv in ref["levels"]],
                }
            res = self.production(wall_id, p, mf[0], mf[1], cover)
            item["production_lam"] = res.lam
            fiber.append(item)
        sampled = []
        for wall_id, cover, p, mf in [("F077-24", None, 120.0, F077_MOMENTS["F077-24"]),
                                      ("L3200-PG-24", 70.0, 120.0, moment_pair(-45))]:
            ref = self.ref.lam(wall_id, cover, p, mf)
            lams = {n: self.production(wall_id, p, mf[0], mf[1], cover, samples=n).lam for n in (24, 72, 720)}
            sampled.append({"case": f"{wall_id} cover={'t/2' if cover is None else cover} P={p} M=({mf[0]:.4f},{mf[1]:.4f})",
                            "samples": lams, "difference_vs_720": {n: lams[n] - lams[720] for n in lams},
                            "reference": ref["lam"],
                            "relative_vs_reference": {n: rel_error(lams[n], ref["lam"]) for n in lams}})
        return {"fiber": fiber, "production_samples": sampled}


def run_validation():
    start = time.perf_counter()
    v = Validation()
    f077 = v.run_f077_case()
    v.run_f077_matrix()
    v.run_layout_matrix()
    v.run_identities()
    v.run_status_cases()
    v.run_self_checks()
    kdev = v.run_kdev_case()
    convergence = v.convergence()
    failures = [c for c in v.comparisons if not c["passed"]]
    counts = {"comparisons": len(v.comparisons), "passed": len(v.comparisons) - len(failures),
              "failed": len(failures)}
    lam_rows = [r for r in v.rows if r["lam_rel_error"] is not None]
    report = {
        "counts": counts,
        "all_pass": not failures,
        "comparisons": v.comparisons,
        "failures": failures,
        "convergence": convergence,
        "f077": f077,
        "kdev_case": kdev,
        "biaxial_rows": v.rows,
        "max_lambda_relative_error": max((r["lam_rel_error"] for r in lam_rows), default=None),
        "max_theta_difference_rad": max((r["theta_difference"] for r in v.rows if r["theta_difference"] is not None), default=None),
        "table_4_used": {ed: {str(k): dict(zip(("S_ungrouted", "S_grouted", "N_ungrouted", "N_grouted"), row))
                              for k, row in rows.items()} for ed, rows in TABLE_4.items()},
        "table_4_source": TABLE_4_SOURCE,
        "tolerances": {"lambda": LAM_TOL, "uniaxial_ip": IP_TOL, "uniaxial_oop": OOP_TOL,
                       "residuals": RESIDUAL_LIMIT, "identities": IDENTITY_TOL, "self": SELF_TOL},
        "fiber_levels_mm": list(H_LEVELS),
        "seconds": time.perf_counter() - start,
    }
    return clean(report)


def print_report(report):
    print("Biaxial independent verification (ramp fiber grid + order-2 Richardson; closed-form uniaxial blocks)")
    print(f"{'group':<30} {'cases':>5} {'checks':>6} {'failed':>6} {'max lam err':>11} {'max dtheta':>10}")
    groups = {}
    for c in report["comparisons"]:
        g = groups.setdefault(c["group"], {"checks": 0, "failed": 0, "cases": 0, "lam": None, "theta": None})
        g["checks"] += 1
        g["failed"] += 0 if c["passed"] else 1
    for row in report["biaxial_rows"]:
        g = groups[row["group"]]
        g["cases"] += 1
        for key, value in (("lam", row["lam_rel_error"]), ("theta", row["theta_difference"])):
            if value is not None:
                g[key] = value if g[key] is None else max(g[key], value)
    for name, g in groups.items():
        lam = "-" if g["lam"] is None else f"{g['lam']:.2e}"
        theta = "-" if g["theta"] is None else f"{g['theta']:.1e}"
        print(f"{name[:30]:<30} {g['cases']:>5} {g['checks']:>6} {g['failed']:>6} {lam:>11} {theta:>10}")
    used = "; ".join(f"{ed} 15 MPa S ({rows[15][0]}, {rows[15][1]}), 20 MPa S ({rows[20][0]}, {rows[20][1]})"
                     for ed, rows in TABLE_4.items())
    print(f"Table 4 f'm (ungrouted, grouted) MPa: {used}")
    for wall_id, f in report["f077"].items():
        print(f"F-077 {f['edition']}: P=120 M=({f['m_ip']}, {f['m_oop']}) production lam={f['production_lam']:.6f} "
              f"(1/lam={f['production_inverse']:.4f}); reference lam={f['reference_lam']:.6f} "
              f"(1/lam={f['reference_inverse']:.4f}); planning 1/lam ~ {f['planning_inverse']}")
    for item in report["convergence"]["fiber"]:
        for label in ("ramp", "cell_centre"):
            data = item[label]
            print(f"fiber {label:<11} {item['case']}: " + ", ".join(
                f"h={h}: {lam:.8f}" for h, lam in zip(item["h"], data["lam"]))
                + f"; extrapolated(p=2) {data['extrapolated_order_2']:.8f}; error ratios "
                + ", ".join("-" if r is None else f"{r:.2f}" for r in data["error_ratio_h_over_h2"]))
        print(f"      production {item['production_lam']:.8f}"
              + ("" if item["closed_form_lam"] is None else f"; closed form {item['closed_form_lam']:.8f}"))
    for item in report["convergence"]["production_samples"]:
        print(f"production samples {item['case']}: " + ", ".join(
            f"{n}: {lam:.9f}" for n, lam in item["samples"].items()) + f"; reference {item['reference']:.7f}")
    print(f"max lambda relative error {report['max_lambda_relative_error']:.3e}; "
          f"max theta difference {report['max_theta_difference_rad']:.3e} rad; {report['seconds']:.1f} s")
    for failure in report["failures"]:
        print("FAIL", failure["group"], failure["case"], failure["quantity"], failure["production"],
              failure["reference"], failure["error"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", help="write the full JSON report to this file")
    args = parser.parse_args(argv)
    report = run_validation()
    print_report(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    print(json.dumps({"biaxial_independent": report["counts"], "all_pass": report["all_pass"]}))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
