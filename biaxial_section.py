"""Plane-section biaxial resistance of the finite wall section (plan WP-E2a; register I-38).

S304-14 10.8 / S304:24 11.8 give no wall equation for combined in-plane and
out-of-plane bending (10.9.3 / 11.9.3 is the column form). This module is the
rational analysis the project reads into them (I-38): plane sections, the
masonry strain eps_mu = 0.003 at the extreme compression corner, a rotated
neutral axis, the equivalent rectangular block of depth beta_1,k c of each
material measured normal to the axis, sigma = 0.85 phi_m f'm,k (no chi, as the
in-plane engine), no masonry tension, elastic-plastic steel (tension <= kdev
fy; compression only with qualifying ties, displaced grout deducted), at a
fixed axial force P along the applied moment direction. It does not
demonstrate compression-zone stability, lateral buckling or span-wise
second-order effects (I-39; those come from the demand records).

Geometry (mm): x along the wall (0..L), y through the thickness (0..t).
Grouted segments of the in-plane section are one rectangle over [0, t] at
f'm,g; hollow segments are two face shells [0, tf] and [t - tf, t] at f'm,ug
(tf of the edition, as the in-plane section). Bars lie at the in-plane bar
positions and at y = t - cover (cover measured as the out-of-plane strip's
cover_to_bar_centre, so its depth d = t - cover from the face y = 0).
Signs: P compression +; M_ip > 0 compresses the end x = 0 (as the in-plane
engine); M_oop > 0 compresses the face y = 0. Moments about the in-plane
section centroid and t/2. Forces kN, moments kNm.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

if __package__:
    from . import in_plane_engine as ip
else:
    import in_plane_engine as ip

THETA_SAMPLES = 72
ROOT_ITERATIONS = 60
C_ITERATIONS = 80
SQUAT_DETAIL = "squat-wall biaxial method outstanding (WP-E2d, register I-40)"
# The interpretation entries this module implements (owned by the in-plane engine register).
BIAXIAL_INTERPRETATIONS = {key: ip.INTERPRETATIONS[key] for key in ("I-38", "I-40")}


@dataclass(frozen=True)
class Region:
    x0: float
    x1: float
    y0: float
    y1: float
    fm: float
    grouted: bool


@dataclass(frozen=True)
class BiaxialSection:
    length: float
    thickness: float
    regions: tuple[Region, ...]
    bars: tuple[tuple[float, float], ...]
    bar_area: float
    fy: float
    kdev: float
    ties: bool
    fm_grouted: float
    centroid_x: float
    centroid_y: float
    squat: bool
    axial_cap: float      # Pr,max, kN (in-plane engine, 0.80 factor)
    tension_cap: float    # Tr, kN
    face_shell: float | None


def build_section(wall: ip.WallInput, cover_to_bar_centre: float | None = None) -> BiaxialSection:
    section = ip.section_properties(wall, "Discrete")
    t = wall.thickness
    cover = t / 2.0 if cover_to_bar_centre is None else cover_to_bar_centre
    if not 0.0 < cover < t:
        raise ip.CalculationError("The bar centre must lie inside the wall thickness.")
    regions = []
    tf = None
    for segment in section.segments:
        if segment.grouted:
            regions.append(Region(segment.x0, segment.x1, 0.0, t, segment.fm, True))
        else:
            tf = segment.effective_thickness / 2.0
            regions.append(Region(segment.x0, segment.x1, 0.0, tf, segment.fm, False))
            regions.append(Region(segment.x0, segment.x1, t - tf, t, segment.fm, False))
    bars = tuple((x, t - cover) for x in section.bar_positions)
    return BiaxialSection(
        wall.length, t, tuple(regions), bars, ip.BAR_AREAS[wall.bar_size], wall.fy,
        ip._development_factor(wall, section), ip._ties_qualify(wall), section.fm_grouted,
        section.centroid, t / 2.0, wall.height / wall.length < 1.0,
        ip._axial_cap(wall, section), ip._tension_cap(wall, section), tf)


def _clip(region: Region, nx: float, ny: float, limit: float) -> tuple[float, float, float]:
    """(area, centroid x, centroid y) of the rectangle part with nx x + ny y <= limit (Sutherland-Hodgman)."""
    corners = [(region.x0, region.y0), (region.x1, region.y0), (region.x1, region.y1), (region.x0, region.y1)]
    values = [nx * x + ny * y - limit for x, y in corners]
    if all(v <= 0.0 for v in values):
        area = (region.x1 - region.x0) * (region.y1 - region.y0)
        return area, (region.x0 + region.x1) / 2.0, (region.y0 + region.y1) / 2.0
    if all(v >= 0.0 for v in values):
        return 0.0, 0.0, 0.0
    polygon = []
    for i in range(4):
        (xa, ya), (xb, yb) = corners[i], corners[(i + 1) % 4]
        va, vb = values[i], values[(i + 1) % 4]
        if va <= 0.0:
            polygon.append((xa, ya))
        if (va < 0.0 < vb) or (vb < 0.0 < va):
            s = va / (va - vb)
            polygon.append((xa + s * (xb - xa), ya + s * (yb - ya)))
    area2 = cx = cy = 0.0
    for i in range(len(polygon)):
        (xa, ya), (xb, yb) = polygon[i], polygon[(i + 1) % len(polygon)]
        cross = xa * yb - xb * ya
        area2 += cross
        cx += (xa + xb) * cross
        cy += (ya + yb) * cross
    if abs(area2) <= 0.0:
        return 0.0, 0.0, 0.0
    return area2 / 2.0, cx / (3.0 * area2), cy / (3.0 * area2)


def _direction(theta: float) -> tuple[float, float]:
    return math.cos(theta), math.sin(theta)


def _origin(section: BiaxialSection, nx: float, ny: float) -> float:
    """n . p of the extreme compression corner of the gross rectangle."""
    return min(nx * x + ny * y for x in (0.0, section.length) for y in (0.0, section.thickness))


def depth_extent(section: BiaxialSection, theta: float) -> float:
    nx, ny = _direction(theta)
    return section.length * abs(nx) + section.thickness * abs(ny)


def resultants(section: BiaxialSection, theta: float, c: float) -> tuple[float, float, float]:
    """(P, M_ip, M_oop) at the neutral-axis depth c (mm, normal to the axis), kN and kNm."""
    if not (c > 0.0 and math.isfinite(c)):
        raise ip.CalculationError("Neutral-axis depth must be positive and finite.")
    nx, ny = _direction(theta)
    origin = _origin(section, nx, ny)
    force = mx = my = 0.0
    for region in section.regions:
        area, x, y = _clip(region, nx, ny, origin + ip.beta_one(region.fm) * c)
        if area <= 0.0:
            continue
        f = 0.85 * ip.PHI_M * region.fm * area
        force += f
        mx += f * (section.centroid_x - x)
        my += f * (section.centroid_y - y)
    block_grout = ip.beta_one(section.fm_grouted) * c
    for x, y in section.bars:
        u = nx * x + ny * y - origin
        strain = ip.EPS_MU * (u - c) / c   # tension +
        if strain > 0.0:
            f = -ip.PHI_S * section.bar_area * min(section.kdev * section.fy, ip.E_S * strain)
        elif section.ties:
            f = ip.PHI_S * section.bar_area * min(section.fy, ip.E_S * -strain)
            if u <= block_grout:
                f = max(0.0, f - 0.85 * ip.PHI_M * section.fm_grouted * section.bar_area)
        else:
            continue
        force += f
        mx += f * (section.centroid_x - x)
        my += f * (section.centroid_y - y)
    return force / 1000.0, mx / 1.0e6, my / 1.0e6


def neutral_axis_at_axial(section: BiaxialSection, theta: float, p: float) -> float | None:
    """c with P(c) = P (bisection; P is non-decreasing in c), or None when P is not reached."""
    extent = depth_extent(section, theta)
    lo, hi = 1.0e-9 * extent, extent / 0.5
    p_lo = resultants(section, theta, lo)[0]
    if p < p_lo - 1.0e-9 * max(1.0, abs(p_lo)):
        return None
    for _ in range(60):
        if resultants(section, theta, hi)[0] >= p:
            break
        hi *= 2.0
    else:
        return None
    scale = max(abs(p), 0.85 * ip.PHI_M * section.fm_grouted * section.length * section.thickness / 1000.0)
    for _ in range(C_ITERATIONS):
        mid = 0.5 * (lo + hi)
        value = resultants(section, theta, mid)[0]
        if abs(value - p) <= 1.0e-12 * scale:
            return mid
        if value < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def moments_at_axial(section: BiaxialSection, theta: float, p: float):
    c = neutral_axis_at_axial(section, theta, p)
    if c is None:
        return None
    force, m_ip, m_oop = resultants(section, theta, c)
    return m_ip, m_oop, c, force


def _theta(section: BiaxialSection, psi: float) -> float:
    """Neutral-axis angle from the sampling angle: tan(theta) = (L/t) tan(psi) clusters the
    samples where the axis turns from cutting the short sides to cutting the long sides,
    where the moment direction changes fastest for a long wall."""
    return math.atan2(section.length * math.sin(psi), section.thickness * math.cos(psi))


@dataclass
class BiaxialResult:
    status: str                        # "ok", "over_capacity", "net_tension", "no_root"
    p: float
    m_ip: float
    m_oop: float
    lam: float | None = None
    theta: float | None = None
    c: float | None = None
    mr_ip: float | None = None
    mr_oop: float | None = None
    residual_direction: float | None = None
    residual_axial: float | None = None
    multiplicity: int = 0
    tension_bars: int = 0
    reversed_lambda: float | None = None
    uniaxial_ip: float | None = None
    uniaxial_oop: float | None = None
    axial_cap: float = 0.0
    squat: bool = False
    notes: list = field(default_factory=list)


def _solve(section: BiaxialSection, p: float, m_ip: float, m_oop: float, samples: int):
    """Smallest load factor lambda with (P, lambda M) on the surface, and its root data."""
    target = math.hypot(m_ip, m_oop)

    def evaluate(psi):
        theta = _theta(section, psi)
        found = moments_at_axial(section, theta, p)
        if found is None:
            return None
        mr_ip, mr_oop, c, force = found
        cross = mr_ip * m_oop - mr_oop * m_ip
        dot = mr_ip * m_ip + mr_oop * m_oop
        return cross, dot, theta, mr_ip, mr_oop, c, force

    grid = [2.0 * math.pi * k / samples for k in range(samples + 1)]
    values = [evaluate(psi) for psi in grid]
    roots = []
    for (a, va), (b, vb) in zip(zip(grid, values), zip(grid[1:], values[1:])):
        if va is None or vb is None:
            continue
        if abs(va[0]) <= 1.0e-12 * math.hypot(va[3], va[4]) * target and va[1] > 0.0:
            roots.append(va)   # the direction is met at a sample (e.g. a pure in-plane moment at theta = 0)
            continue
        if va[0] * vb[0] > 0.0 or max(va[1], vb[1]) <= 0.0:
            continue
        lo, hi, flo = a, b, va[0]
        best = vb
        for _ in range(ROOT_ITERATIONS):
            mid = 0.5 * (lo + hi)
            vm = evaluate(mid)
            if vm is None:
                break
            best = vm
            if vm[0] == 0.0:
                break
            if (vm[0] < 0.0) == (flo < 0.0):
                lo, flo = mid, vm[0]
            else:
                hi = mid
        if best[1] > 0.0:
            roots.append(best)
    solutions, seen = [], []
    for cross, dot, theta, mr_ip, mr_oop, c, force in roots:
        angle = theta % (2.0 * math.pi)
        if any(min(abs(angle - other), 2.0 * math.pi - abs(angle - other)) <= 1.0e-9 for other in seen):
            continue
        seen.append(angle)
        magnitude = math.hypot(mr_ip, mr_oop)
        solutions.append((magnitude / target, theta, c, mr_ip, mr_oop, force,
                          abs(cross) / (magnitude * target) if magnitude > 0.0 else math.inf))
    if not solutions:
        return None
    solutions.sort(key=lambda item: item[0])
    best = solutions[0]
    multiplicity = sum(1 for item in solutions if abs(item[0] - best[0]) <= 1.0e-9 * max(1.0, best[0]))
    return best, multiplicity


def uniaxial_limit(section: BiaxialSection, p: float, plane: str, sense: float = 1.0) -> float | None:
    """|M| at P in one plane: in-plane theta 0 / pi, out-of-plane pi/2 / 3pi/2."""
    theta = {"ip": 0.0, "oop": math.pi / 2.0}[plane] + (0.0 if sense >= 0.0 else math.pi)
    found = moments_at_axial(section, theta, p)
    if found is None:
        return None
    return abs(found[0] if plane == "ip" else found[1])


def biaxial_resistance(wall: ip.WallInput, p: float, m_ip: float, m_oop: float, *,
                       cover_to_bar_centre: float | None = None, samples: int = THETA_SAMPLES) -> BiaxialResult:
    """Biaxial resistance at fixed P along the applied moment direction (register I-38).

    status "ok": lam is the factor with (P, lam M_ip, lam M_oop) on the
    resistance surface (lam >= 1 passes); "over_capacity": P > Pr,max;
    "net_tension": P < 0 (unsupported); "no_root": no section state carries P
    in that direction. The reversed out-of-plane sense is solved as a
    diagnostic (reversed_lambda), never substituted.
    """
    section = build_section(wall, cover_to_bar_centre)
    result = BiaxialResult("ok", p, m_ip, m_oop, axial_cap=section.axial_cap, squat=section.squat)
    if p < 0.0:
        result.status = "net_tension"
        return result
    if p > section.axial_cap + 1.0e-9:
        result.status = "over_capacity"
        return result
    result.uniaxial_ip = uniaxial_limit(section, p, "ip", m_ip if m_ip else 1.0)
    result.uniaxial_oop = uniaxial_limit(section, p, "oop", m_oop if m_oop else 1.0)
    if m_ip == 0.0 and m_oop == 0.0:
        result.lam = math.inf
        return result
    solved = _solve(section, p, m_ip, m_oop, samples)
    if solved is None:
        result.status = "no_root"
        return result
    (lam, theta, c, mr_ip, mr_oop, force, residual), multiplicity = solved
    nx, ny = _direction(theta)
    origin = _origin(section, nx, ny)
    result.lam, result.theta, result.c, result.mr_ip, result.mr_oop = lam, theta, c, mr_ip, mr_oop
    result.residual_direction = residual
    result.residual_axial = abs(force - p) / max(abs(p), 0.85 * ip.PHI_M * section.fm_grouted
                                                 * section.length * section.thickness / 1000.0)
    result.multiplicity = multiplicity
    result.tension_bars = sum(1 for x, y in section.bars if nx * x + ny * y - origin > c)
    if m_oop != 0.0:
        reversed_solution = _solve(section, p, m_ip, -m_oop, samples)
        result.reversed_lambda = reversed_solution[0][0] if reversed_solution else None
    return result


__all__ = ["BIAXIAL_INTERPRETATIONS", "BiaxialResult", "BiaxialSection", "Region", "SQUAT_DETAIL",
           "biaxial_resistance", "build_section", "depth_extent", "moments_at_axial", "neutral_axis_at_axial",
           "resultants", "uniaxial_limit"]
