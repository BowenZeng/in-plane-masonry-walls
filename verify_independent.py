"""Independent section-equilibrium and shear checks against the supplied editions.

Uses only WallInput/calculate_wall from production; material tables, integrations,
equilibrium root solving and resistance equations are independently evaluated.
The June 2025 errata are deliberately outside this audit's authorized basis.

Face shells (plan WP-unit): S304-14 Ae uses the declared mortar-bedded face
shell and S304:24 Ae the declared Clause 3.1 minimum face shell.

Pd follows the case rule (plan WP2): Pd_case = 0.9 D_cnt + min(0, N_lat), with
D_cnt = 150 kN + the wall weight and N_lat the case's lateral axial load; in this
matrix the tension in Pf is taken as the lateral uplift, N_lat = min(0, Pf). The
reference forms that Pd itself and the engine receives it as the case Pd.
"""

from dataclasses import replace
from itertools import product
import math

from in_plane_engine import WallInput, calculate_wall


# Supplied CSA Table 4: (ungrouted, solid/grouted), MPa.
STRENGTHS = {
    ("S304-14", "Type S", 15): (10.0, 7.5),
    ("S304-14", "Type S", 30): (17.5, 13.5),
    ("S304-14", "Type N", 15): (8.0, 6.0),
    ("S304-14", "Type N", 30): (12.0, 9.0),
    ("S304:24", "Type S", 15): (12.0, 9.0),
    ("S304:24", "Type S", 30): (20.5, 16.0),
    ("S304:24", "Type N", 15): (9.5, 7.0),
    ("S304:24", "Type N", 30): (14.0, 10.5),
}


def reference(wall):
    length, height, thickness = wall.length, wall.height, wall.thickness
    ug, fg = STRENGTHS[wall.edition, wall.mortar_type, wall.nominal_unit_strength]
    reinforced = wall.wall_type != "Unreinforced masonry"
    partial = wall.wall_type == "Partially grouted reinforced masonry"
    bars = [100 + i * (length - 200) / 4 for i in range(5)] if reinforced else []
    tf = wall.minimum_face_shell_thickness if wall.edition == "S304:24" else wall.face_shell_thickness
    # Every verification bar is 15M, centred at 100, 1300, ... 4900 mm.
    if partial:
        segments = []
        cursor = 0.0
        for bar in bars:
            start, end = bar - 100, bar + 100
            if cursor < start:
                segments.append((cursor, start, 2 * tf, ug))
            segments.append((start, end, thickness, fg))
            cursor = end
        if cursor < length:
            segments.append((cursor, length, 2 * tf, ug))
        filled_fraction = 1000 / length
    elif not reinforced and wall.unreinforced_condition == "Hollow ungrouted":
        segments = [(0, length, 2 * tf, ug)]
        filled_fraction = 0.0
    else:
        segments = [(0, length, thickness, fg)]
        filled_fraction = 1.0
    area = sum((b - a) * t for a, b, t, _ in segments)
    fm = sum((b - a) * t * f for a, b, t, f in segments) / area
    cap = 0.8 * 0.85 * 0.6 * area * fm / 1000
    gamma = 1.0 if filled_fraction == 1 else min(0.5, area / (length * thickness))
    if partial and wall.partial_grout_method == "Smeared":
        segments = [(0, length, area / length, fm)]

    def forces(depth):
        p, m = 0.0, 0.0
        tensile_forces = []
        for a, b, t, f in segments:
            edge = min(b, (0.8 - 0.01 * max(0, f - 20)) * depth)
            if edge > a:
                force = 0.51 * f * t * (edge - a)
                p += force
                m += force * (length / 2 - (edge + a) / 2)
        for x in bars:
            tension = 0.85 * 200 * min(400, max(0, 600 * (x / depth - 1)))
            p -= tension
            m += tension * (x - length / 2)
            if tension > 0:
                tensile_forces.append((x, tension))
        if height < length and tensile_forces:
            reduced_depth = min(0.67 * length, 0.7 * height)
            if wall.edition == "S304:24":
                reduced_depth = min(reduced_depth, sum(x for x, _ in tensile_forces) / len(tensile_forces))
            reduction = sum(tension * (x - reduced_depth) for x, tension in tensile_forces)
            m -= max(0, reduction)
        if not tensile_forces:
            m = min(m, max(p, 0) * 0.33 * length)
        return p / 1000, max(0, m) / 1e6

    if reinforced:
        low, high = 1e-8, 2 * length
        for _ in range(65):
            c = (low + high) / 2
            if forces(c)[0] < wall.factored_axial:
                low = c
            else:
                high = c
        mr = forces((low + high) / 2)[1]
    else:
        ft = (0.65 if wall.mortar_type == "Type S" else 0.50) if wall.unreinforced_condition == "Fully grouted hollow" else (0.40 if wall.mortar_type == "Type S" else 0.30)
        z = area * length / 6
        n = wall.factored_axial * 1000
        elastic = max(0, min((0.36 * fm - n / area) * z, (0.6 * ft + n / area) * z))
        block = n / (0.51 * fm * (area / length))
        cracked = min(n * (length / 2 - block / 2), n * 0.33 * length)
        mr = max(elastic, cracked) / 1e6

    volume = length * height * thickness / 1e9
    density = 2100 if not reinforced and wall.unreinforced_condition == "Solid units" else 0.56 * 2100 + 0.44 * filled_fraction * 2350
    weight = volume * density * 9.81 / 1000
    pd = 0.9 * (150 + weight) + min(0.0, wall.factored_axial)
    dv = 0.8 * length
    shear_n = wall.factored_shear * 1000
    moment_nmm = wall.factored_moment * 1e6
    ratio = min(1, max(0.25, moment_nmm / (shear_n * dv))) if shear_n else 1.0
    av = wall.horizontal_reinforcement_area
    if wall.edition == "S304-14" or not reinforced:
        vm = 0.16 * (2 - ratio) * (math.sqrt(fm) if wall.edition == "S304-14" or wall.apply_proposed_8_10_2_3_correction else 1)
        masonry = 0.6 * (vm * thickness * dv + 0.25 * max(0.0, pd) * 1000) * gamma
        steel = 0.6 * 0.85 * av * 400 * dv / 200 if reinforced else 0
        maximum = 0.4 * 0.6 * math.sqrt(fm) * thickness * dv * gamma
        vr = min(masonry + steel, maximum) / 1000
    else:
        maximum = 0.25 * 0.6 * fm * thickness * dv * gamma
        if partial:
            beta = 0.16 * (2 - ratio) + 0.25 * max(0.0, pd) * 1000 / (math.sqrt(fm) * thickness * dv)
            routes = [(beta, 59), (beta, 59)]
        else:
            minimum = 0.35 * thickness * 200 / 400
            beta = 0.18 if av >= minimum else 230 / (1000 + 1.4 * dv)
            ze = 300 if av >= minimum else 1.4 * dv
            eps = max(0, min(0.003, (max(moment_nmm, shear_n * dv) / dv + shear_n - 500 * wall.factored_axial) / (2 * 200000 * 600)))
            if wall.factored_axial < 0:
                eps = min(0.003, 2 * eps)
            routes = [(beta, 42), (0.4 / (1 + 1500 * eps) * 1300 / (1000 + ze), 29 + 7000 * eps)]
        capacities = []
        for beta, angle in routes:
            vm = 0.6 * beta * math.sqrt(fm) * thickness * dv * gamma
            vs = 0.85 * av * 400 * min(dv / math.tan(math.radians(angle)), height) / 200
            capacities.append(min(vm + vs, maximum) / 1000)
        vr = capacities[1] if wall.factored_axial < 0 or wall.shear_method == "General" else capacities[0] if wall.shear_method == "Simplified" else min(capacities)
    # Clamping force C = max(0, Pd_case + phi_s As fy), never Pf (register I-3).
    slide = (0.6 if wall.edition == "S304-14" else 1) * max(0, pd + (340 if reinforced else 0))
    return area, cap, mr, weight, pd, vr, slide


def main():
    routes = [
        ("Fully grouted reinforced masonry", "Discrete", "Hollow ungrouted"),
        ("Partially grouted reinforced masonry", "Discrete", "Hollow ungrouted"),
        ("Partially grouted reinforced masonry", "Smeared", "Hollow ungrouted"),
        *[("Unreinforced masonry", "Discrete", condition) for condition in ("Hollow ungrouted", "Fully grouted hollow", "Solid units")],
    ]
    count = 0
    largest_mr_error = 0.0
    for edition, mortar, strength, route, load_index in product(("S304-14", "S304:24"), ("Type S", "Type N"), (15, 30), routes, range(4)):
        kind, method, condition = route
        reinforced = kind != "Unreinforced masonry"
        thickness = (140, 190, 240, 290)[load_index]
        wall = WallInput(
            edition=edition, mortar_type=mortar, nominal_unit_strength=strength,
            wall_type=kind, partial_grout_method=method, unreinforced_condition=condition,
            thickness=thickness, face_shell_thickness={140: 26, 190: 36.2, 240: 38.6, 290: 41.4}[thickness],
            minimum_face_shell_thickness={140: 25, 190: 30, 240: 35, 290: 35}[thickness],
            height=(3000, 5000, 7500, 5000)[load_index], bar_count=5 if reinforced else 0,
            horizontal_bar_size="15M", horizontal_anchorage_confirmed=True, horizontal_continuity_confirmed=True,
            factored_shear=(0, 100, 100, 250)[load_index], factored_moment=(0, 50, 800, 200)[load_index],
            shear_method=("Simplified", "General", "Use lower of simplified and general", "General")[load_index],
        )
        cap = reference(wall)[1]
        axial_loads = [0, 0.3 * cap, 0.9 * cap] + ([-272.0] if reinforced else [])
        corrections = (False, True) if (not reinforced and edition == "S304:24") else (False,)
        for axial, av, correction in product(axial_loads, (0, 200) if reinforced else (0,), corrections):
            case = replace(wall, factored_axial=axial, horizontal_reinforcement_area=av, horizontal_reinforcement_type="Bond beam bars" if av else "None", apply_proposed_8_10_2_3_correction=correction)
            expected = reference(case)
            actual = calculate_wall(replace(case, dead_load_compression=expected[4], pd_basis="independent case rule"))
            calculated = (actual.section.area, actual.axial_cap, actual.moment_resistance, actual.self_weight, actual.pd, actual.shear.resistance, actual.sliding.resistance)
            for index, (got, want) in enumerate(zip(calculated, expected)):
                tolerance = max(1e-7, abs(want) * 1e-10)
                assert got is not None and abs(got - want) <= tolerance, (count, case, index, got, want)
            largest_mr_error = max(largest_mr_error, abs(actual.moment_resistance - expected[2]))
            count += 1
    print(f"INDEPENDENT_EQUATIONS=PASS cases={count}; maximum Mr equilibrium difference={largest_mr_error:.9f} kN-m")


if __name__ == "__main__":
    main()
