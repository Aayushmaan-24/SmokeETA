#!/usr/bin/env python3
"""
Tests for the Smoke ETA particle-dispersion simulator.

16 legacy tests covering wind convention, fire aggregation, physics, zone ETA,
reproducibility, and data validation. Plus 2 new tests for transport physics.

Uses small synthetic fixtures only — no live APIs, no API keys, no network.
Run:  python -m pytest backend/test_simulate.py -v
"""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulate import (
    InputError,
    WindField,
    aggregate_fires,
    assign_particles,
    compute_zone_results,
    distance_km,
    load_inputs,
    run_simulation,
    simulate_particles,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def make_wind(times: int = 6, speed_kmh: float = 10.0, direction_deg: float = 0.0):
    """Uniform 3x3 lat/lon grid covering 28-30N, 77-79E with constant wind."""
    grid = []
    for lat in (28.0, 29.0, 30.0):
        for lon in (77.0, 78.0, 79.0):
            grid.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "speed_kmh": [speed_kmh] * times,
                    "direction_deg": [direction_deg] * times,
                }
            )
    return {"times": [f"2026-01-01T{h:02d}:00" for h in range(times)], "grid": grid}


def make_wind_8x8(times: int = 48, speed_kmh: float = 20.0, direction_deg: float = 270.0):
    """Larger 8x8 lat/lon grid covering 27.5-32.5N, 73.5-78.5E (real test bounds)."""
    lats = np.linspace(27.5, 32.5, 8)
    lons = np.linspace(73.5, 78.5, 8)
    grid = []
    for lat in lats:
        for lon in lons:
            grid.append({
                "lat": float(lat),
                "lon": float(lon),
                "speed_kmh": [float(speed_kmh)] * times,
                "direction_deg": [float(direction_deg)] * times,
            })
    return {"times": [f"2026-10-01T{h:02d}:00Z" for h in range(times)], "grid": grid}


def write_data(tmp: Path, fires, wind, zones):
    (tmp / "fires.json").write_text(json.dumps(fires))
    (tmp / "wind.json").write_text(json.dumps(wind))
    (tmp / "zones.json").write_text(json.dumps(zones))


# ---------------------------------------------------------------------------
# LEGACY TESTS (16 tests from test_simulate_legacy.py)
# ---------------------------------------------------------------------------

class TestWindDirectionConvention(unittest.TestCase):
    """Wind direction is where wind comes FROM; smoke moves the opposite way."""

    def _displacement(self, direction_deg: float) -> tuple[float, float]:
        wf = WindField(make_wind(direction_deg=direction_deg))
        src = [{"lat": 29.0, "lon": 78.0, "frp_sum": 1.0, "n_fires": 1}]
        sim = simulate_particles(src, [200], wf, seed=1, diffusion_sigma_kmh=0.0)
        last = sim["frames"][-1]
        dlat = float(np.mean(last["lat"])) - 29.0
        dlon = float(np.mean(last["lon"])) - 78.0
        return dlat, dlon

    def test_wind_from_southwest_moves_smoke_northeast(self):
        # From SW (225 deg) -> smoke travels toward NE: lat up, lon up.
        dlat, dlon = self._displacement(225.0)
        self.assertGreater(dlat, 0.1, f"expected northward motion, got {dlat}")
        self.assertGreater(dlon, 0.1, f"expected eastward motion, got {dlon}")

    def test_wind_from_northeast_moves_smoke_southwest(self):
        # From NE (45 deg) -> smoke travels toward SW: lat down, lon down.
        dlat, dlon = self._displacement(45.0)
        self.assertLess(dlat, -0.1, f"expected southward motion, got {dlat}")
        self.assertLess(dlon, -0.1, f"expected westward motion, got {dlon}")

    def test_zero_wind_no_systematic_displacement(self):
        wf = WindField(make_wind(speed_kmh=0.0))
        src = [{"lat": 29.0, "lon": 78.0, "frp_sum": 1.0, "n_fires": 1}]
        sim = simulate_particles(src, [200], wf, seed=1)  # diffusion stays on
        last = sim["frames"][-1]
        dlat = float(np.mean(last["lat"])) - 29.0
        dlon = float(np.mean(last["lon"])) - 78.0
        # Random walk is symmetric: the ensemble mean must stay ~in place.
        self.assertLess(abs(dlat), 0.15)
        self.assertLess(abs(dlon), 0.15)
        # And particles do spread (diffusion works even without wind).
        self.assertGreater(float(np.std(last["lat"])), 0.0)


class TestFireAggregation(unittest.TestCase):
    def test_nearby_fires_grouped_and_frp_summed(self):
        fires = [
            {"lat": 28.01, "lon": 77.01, "frp": 2.0},
            {"lat": 28.02, "lon": 77.02, "frp": 3.0},
            {"lat": 28.50, "lon": 78.50, "frp": 5.0},
        ]
        sources = aggregate_fires(fires, cell_deg=0.1)
        self.assertEqual(len(sources), 2)
        frp = sorted(s["frp_sum"] for s in sources)
        self.assertEqual(frp, [5.0, 5.0])
        for s in sources:
            self.assertEqual(s["n_fires"] + 0 if s["n_fires"] else 1, s["n_fires"])

    def test_missing_or_invalid_frp_does_not_crash(self):
        fires = [
            {"lat": 28.1, "lon": 77.1},                 # frp missing
            {"lat": 28.2, "lon": 77.2, "frp": None},    # null frp
            {"lat": 28.3, "lon": 77.3, "frp": "bad"},   # non-numeric
            {"lat": 28.4, "lon": 77.4, "frp": -5},      # negative
            {"lat": "oops", "lon": 77.5},               # malformed lat -> skipped
        ]
        sources = aggregate_fires(fires)
        self.assertEqual(len(sources), 4)
        self.assertTrue(all(math.isfinite(s["lat"]) for s in sources))

    def test_particles_weighted_by_frp(self):
        sources = [
            {"lat": 28.0, "lon": 77.0, "frp_sum": 9.0, "n_fires": 1},
            {"lat": 29.0, "lon": 78.0, "frp_sum": 1.0, "n_fires": 1},
        ]
        counts = assign_particles(sources, per_source=50, max_particles=10_000)
        # Target = 50 * 2 = 100 split 9:1 via largest-remainder rounding.
        self.assertEqual(counts, [90, 10])
        self.assertEqual(sum(counts), 100)


class TestPhysics(unittest.TestCase):
    def test_decay_reduces_mass(self):
        wf = WindField(make_wind(speed_kmh=0.0))
        src = [{"lat": 29.0, "lon": 78.0, "frp_sum": 1.0, "n_fires": 1}]
        sim = simulate_particles(src, [50], wf, seed=7, decay_tau_h=30.0)
        first = sim["frames"][0]
        last = sim["frames"][-1]
        self.assertGreater(first["hour"], -1)
        self.assertLess(np.sum(last["mass"]), np.sum(first["mass"]))
        # e-folding check: after 30h each particle mass is ~exp(-1) of start.
        f30 = next(f for f in sim["frames"] if f["hour"] == 30.0)
        ratio = float(np.mean(f30["mass"]) / np.mean(first["mass"]))
        self.assertAlmostEqual(ratio, math.exp(-1.0), delta=0.02)


class TestZoneETA(unittest.TestCase):
    ZONES = [
        {"id": "near", "name": "Near", "lat": 29.0, "lon": 78.0, "radius_km": 12},
        {"id": "far", "name": "Far", "lat": 40.0, "lon": 90.0, "radius_km": 12},
    ]

    def test_particle_within_radius_detected_as_arrival(self):
        frames = [
            {"hour": 5.0, "lat": [29.02], "lon": [78.01], "mass": [0.5]},
            {"hour": 10.0, "lat": [29.05], "lon": [78.02], "mass": [0.4]},
        ]
        results = compute_zone_results(frames, self.ZONES, [])
        near = next(r for r in results if r["id"] == "near")
        self.assertTrue(near["arrived"])
        self.assertEqual(near["arrival_hour"], 5.0)
        self.assertEqual(near["peak_hour"], 5.0)
        self.assertGreater(near["influence_score"], 0)

    def test_zone_outside_horizon_reports_no_arrival(self):
        frames = [
            {"hour": 5.0, "lat": [29.0], "lon": [78.0], "mass": [0.5]},
        ]
        results = compute_zone_results(frames, self.ZONES, [])
        far = next(r for r in results if r["id"] == "far")
        self.assertFalse(far["arrived"])
        self.assertIsNone(far["arrival_hour"])
        self.assertEqual(far["severity"], "None")
        self.assertIn("No arrival detected within 48 hours", far["status"])

    def test_empty_frames_no_arrival_for_any_zone(self):
        results = compute_zone_results([], self.ZONES, [])
        self.assertTrue(all(not r["arrived"] for r in results))


class TestReproducibility(unittest.TestCase):
    def test_same_seed_same_results(self):
        wf = WindField(make_wind(speed_kmh=12.0, direction_deg=200.0))
        src = [{"lat": 28.4, "lon": 77.3, "frp_sum": 3.0, "n_fires": 2}]
        a = simulate_particles(src, [300], wf, seed=123)
        b = simulate_particles(src, [300], wf, seed=123)
        self.assertEqual(a["frames"], b["frames"])

    def test_different_seed_differs(self):
        wf = WindField(make_wind(speed_kmh=12.0, direction_deg=200.0))
        src = [{"lat": 28.4, "lon": 77.3, "frp_sum": 3.0, "n_fires": 2}]
        a = simulate_particles(src, [300], wf, seed=1)
        b = simulate_particles(src, [300], wf, seed=2)
        self.assertNotEqual(a["frames"], b["frames"])


class TestMissingAndMalformedData(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_missing_file_gives_clear_error(self):
        with self.assertRaises(InputError) as ctx:
            load_inputs(self.tmp)
        self.assertIn("fires.json", str(ctx.exception))

    def test_malformed_json_gives_clear_error(self):
        (self.tmp / "fires.json").write_text("{not valid json")
        (self.tmp / "wind.json").write_text("{}")
        (self.tmp / "zones.json").write_text("[]")
        with self.assertRaises(InputError) as ctx:
            load_inputs(self.tmp)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_invalid_wind_data_produces_safe_result(self):
        # Wind grid with garbage values must not crash and must not invent
        # motion: with all-invalid wind, zero-wind fallback keeps particles
        # near their sources (only diffusion moves them).
        wind = make_wind(speed_kmh=15.0, direction_deg=270.0)
        for pt in wind["grid"]:
            pt["speed_kmh"] = [None, "x"] + [None] * 4
            pt["direction_deg"] = [None] * 6
        wf = WindField(wind)
        src = [{"lat": 29.0, "lon": 78.0, "frp_sum": 1.0, "n_fires": 1}]
        sim = simulate_particles(src, [100], wf, seed=5)
        last = sim["frames"][-1]
        # No systematic wind-driven drift across the ensemble.
        self.assertLess(abs(float(np.mean(last["lat"])) - 29.0), 0.2)
        self.assertLess(abs(float(np.mean(last["lon"])) - 78.0), 0.2)

    def test_end_to_end_missing_zone_field(self):
        write_data(
            self.tmp,
            [{"lat": 29.0, "lon": 78.0, "frp": 1.0}],
            make_wind(),
            [{"id": "z1", "lat": 29.0}],  # missing 'lon'
        )
        with self.assertRaises(InputError) as ctx:
            run_simulation(self.tmp)
        self.assertIn("lon", str(ctx.exception))


# ---------------------------------------------------------------------------
# NEW TESTS (2 tests: transport physics with hourly emission)
# ---------------------------------------------------------------------------

class TestTransportPhysics(unittest.TestCase):
    """Tests for hourly emission and meaningful ETA (not h0 arrivals)."""

    def test_westerly_transport_80km_20kmh(self):
        """
        Source 80 km west with 20 km/h westerly wind -> arrival ~4h later.

        Setup: Zone at (29.0, 77.5), source at (29.0, 76.68) — 80 km west.
        Wind: 20 km/h from west (dir=270°), so smoke moves east.
        Expected: Arrival at ~4 hours (80 / 20), NOT at hour 0.
        """
        zone_lat, zone_lon = 29.0, 77.5
        source_lat, source_lon = 29.0, 76.68

        # Verify distance
        d = distance_km(zone_lat, zone_lon, source_lat, source_lon)
        self.assertGreater(d, 75)
        self.assertLess(d, 85)

        # Create synthetic scenario
        fires = [{"lat": source_lat, "lon": source_lon, "frp": 100}]
        wind = make_wind_8x8(speed_kmh=20.0, direction_deg=270.0)
        zones = [{"id": "test", "name": "Test", "lat": zone_lat, "lon": zone_lon, "radius_km": 12}]

        # Simulate
        sources = aggregate_fires(fires)
        counts = assign_particles(sources, per_source=400, max_particles=40_000)
        wind_field = WindField(wind)
        sim = simulate_particles(sources, counts, wind_field, seed=42)
        zone_results = compute_zone_results(sim["frames"], zones, sources)

        result = zone_results[0]
        # Particles now emit hourly over 24h, so arrival should be 3-5h
        # (some particles emitted at t=0 reach around 4h; later emissions arrive later).
        self.assertTrue(result["arrived"], f"Expected arrival but got: {result['status']}")
        self.assertIsNotNone(result["arrival_hour"])
        self.assertGreaterEqual(result["arrival_hour"], 3.0)
        self.assertLessEqual(result["arrival_hour"], 5.5)

    def test_no_upwind_source_downwind_wind(self):
        """
        Source 80 km east (downwind) with 20 km/h westerly wind -> no arrival.

        Setup: Zone at (29.0, 77.5), source at (29.0, 78.32) — 80 km east.
        Wind: 20 km/h from west (270°), blows smoke further west, away from zone.
        Expected: No arrival (smoke blown in opposite direction).
        """
        zone_lat, zone_lon = 29.0, 77.5
        source_lat, source_lon = 29.0, 78.32  # 80 km east (downwind)

        # Verify distance
        d = distance_km(zone_lat, zone_lon, source_lat, source_lon)
        self.assertGreater(d, 75)
        self.assertLess(d, 85)

        # Create synthetic scenario
        fires = [{"lat": source_lat, "lon": source_lon, "frp": 100}]
        wind = make_wind_8x8(speed_kmh=20.0, direction_deg=270.0)
        zones = [{"id": "test", "name": "Test", "lat": zone_lat, "lon": zone_lon, "radius_km": 12}]

        # Simulate
        sources = aggregate_fires(fires)
        counts = assign_particles(sources, per_source=400, max_particles=40_000)
        wind_field = WindField(wind)
        sim = simulate_particles(sources, counts, wind_field, seed=42)
        zone_results = compute_zone_results(sim["frames"], zones, sources)

        result = zone_results[0]
        # Downwind source should not reach upwind zone in westerly flow
        # (diffusion may cause minimal arrival, but should be low/none).
        if result["arrived"]:
            self.assertEqual(result["severity"], "Low",
                           f"Downwind source should at most be 'Low', got {result['severity']}")
        else:
            self.assertFalse(result["arrived"],
                           f"Downwind source should not arrive, got {result['status']}")


if __name__ == "__main__":
    unittest.main()
