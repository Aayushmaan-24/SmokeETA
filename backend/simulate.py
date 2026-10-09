#!/usr/bin/env python3
"""
Smoke ETA — particle-dispersion simulation backend.

Reads the JSON files produced by fetch_data.py (fires, wind grid, zones),
runs a 48-hour Lagrangian particle simulation on a 10-minute timestep, and
writes per-zone smoke arrival estimates to data/sim.json.

This is a hackathon prototype, NOT a scientifically validated atmospheric
model. All severity/influence values are relative, not calibrated AQI
predictions. Current AQI data (data/aqi.json) is contextual only and is
never used as a substitute for simulated smoke arrival.

Usage:
    python backend/simulate.py            # run and write data/sim.json
    python backend/simulate.py --info     # print output schema documentation

Output schema (data/sim.json):
{
  "meta": {
    "generated_at": ISO timestamp,
    "duration_hours": 48,
    "timestep_minutes": 10,
    "total_particles": int,
    "random_seed": int,
    "sources": [{lat, lon, frp_sum, n_fires, particles, rel_weight}],
    "assumptions": [str, ...],
    "limitations": [str, ...]
  },
  "frames": [                         # one per output frame (hourly)
    {"hour": h,
     "particles": [{"lat","lon","mass"}, ...],   # live particles, mass is relative
     "zone_influence": {zone_id: relative_influence_at_this_hour}}
  ],
  "zones": [
    {"id","name","lat","lon","radius_km",
     "local_fires_nearby": bool,
     "arrival_hour": float|null, "peak_hour": float|null,
     "peak_influence": float,        # normalized 0..1
     "influence_score": float,       # normalized 0..1 (mean influence)
     "severity": "Low"|"Moderate"|"High"|"Very High"|"None",
     "status": str,
     "arrived": bool}
  ]
}

Wind grid notes:
- Wind grid points form a regular 8x8 lat/lon lattice, so bilinear spatial
  interpolation is used. If the grid is irregular/sparse, the code falls back
  to inverse-distance weighting (IDW) from the nearest grid points.
- direction_deg follows the meteorological convention: the direction the wind
  comes FROM. Smoke is transported TOWARD the opposite direction.
- Particles outside the wind grid keep their last known wind (advection
  freezes at the boundary, decay continues) — no silent zero-wind assumption.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import warnings
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Configuration (all tunables in one place)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

DURATION_HOURS = 48.0
TIMESTEP_MIN = 10
TIMESTEP_H = TIMESTEP_MIN / 60.0
OUTPUT_FRAME_INTERVAL_H = 1.0  # frontend timeline granularity

DEFAULT_PARTICLES_PER_SOURCE = 400
MAX_PARTICLES = 40_000          # hard cap for computational practicality
MAX_PARTICLES_PER_FRAME = 2500  # per-frame display cap to keep sim.json small
DIFFUSION_SIGMA_KMH = 1.0       # random-walk velocity jitter (km/h)
DECAY_TAU_H = 30.0              # e-folding time for particle mass (hours)
DEFAULT_ZONE_RADIUS_KM = 12.0

# Particle emission: hourly over first 24 hours
EMISSION_HOURS = 24.0

# Local fire distance threshold
LOCAL_FIRE_DISTANCE_KM = 30.0

# Arrival threshold: fraction of total emitted mass in zone catchment
ARRIVAL_MASS_THRESHOLD = 0.001  # 0.1% of total emitted mass

ARRIVAL_MASS_THRESHOLD = 0.001  # 0.1% of total emitted mass

# Severity thresholds: normalized influence score bands (Low / Moderate / High / Very High).
# Tuned on 2024-11-01 replay (high fire activity, favorable wind) to spread 11 zones across
# severity buckets: ~3 zones per Low/Moderate/High/Very High. Based on 25th/50th/75th percentiles
# of influence_score distribution to match real spatial/temporal dispersion patterns.
SEVERITY_THRESHOLDS = (0.110, 0.113, 0.124)   # Low / Moderate / High / Very High

DEFAULT_SEED = 42

# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


class InputError(Exception):
    """Raised when input files are missing, malformed, or lack required data."""


def load_json(path: Path, what: str) -> Any:
    """Load a JSON file with clear error messages."""
    if not path.exists():
        raise InputError(f"{what} file not found: {path}. Run backend/fetch_data.py first.")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise InputError(f"{what} file is not valid JSON ({path}): {e}") from e


def load_inputs(
    data_dir: Path = DATA_DIR,
) -> tuple[list[dict], dict[str, Any], list[dict]]:
    """Load and validate fires.json, wind.json and zones.json."""
    fires = load_json(data_dir / "fires.json", "Fires")
    if not isinstance(fires, list) or not fires:
        raise InputError(
            "fires.json must be a non-empty list of fire hotspots. "
            "If there are truly no active fires, the simulation has no sources."
        )

    wind = load_json(data_dir / "wind.json", "Wind")
    if not isinstance(wind, dict):
        raise InputError("wind.json must be an object with 'times' and 'grid'.")
    times = wind.get("times")
    grid = wind.get("grid")
    if not isinstance(times, list) or not times:
        raise InputError("wind.json 'times' must be a non-empty list of ISO timestamps.")
    if not isinstance(grid, list) or len(grid) < 2:
        raise InputError("wind.json 'grid' must be a list with at least 2 points.")

    zones = load_json(data_dir / "zones.json", "Zones")
    if not isinstance(zones, list) or not zones:
        raise InputError("zones.json must be a non-empty list of zones.")

    for i, z in enumerate(zones):
        for field in ("id", "lat", "lon"):
            if field not in z:
                raise InputError(f"Zone #{i} in zones.json is missing required field '{field}'.")

    return fires, wind, zones


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def km_per_degree() -> tuple[float, float]:
    """Return (km per degree lat, km per degree lon) at mid-Delhi latitude."""
    lat0 = 29.0
    km_lat = 111.32
    km_lon = 111.32 * math.cos(math.radians(lat0))
    return km_lat, km_lon


def delta_lat_lon(
    dx_km: float, dy_km: float, km_lat: float, km_lon: float
) -> tuple[float, float]:
    """Convert a km displacement (east=+, north=+) to lat/lon degrees."""
    return dy_km / km_lat, dx_km / km_lon


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute distance in km between two lat/lon points."""
    km_lat, km_lon = km_per_degree()
    return math.sqrt(
        ((lat1 - lat2) * km_lat) ** 2 + ((lon1 - lon2) * km_lon) ** 2
    )


# ---------------------------------------------------------------------------
# Fire-source preparation
# ---------------------------------------------------------------------------


def aggregate_fires(
    fires: list[dict], cell_deg: float = 0.1
) -> list[dict[str, Any]]:
    """
    Group fire hotspots into ~cell_deg lat/lon grid cells and sum FRP per cell.

    FRP is used only as a relative emission weight, not as an absolute PM2.5
    measure. Missing/invalid FRP is treated as 0 for weighting, but the fire
    still counts toward n_fires so it is not silently dropped.
    """
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for f in fires:
        try:
            lat, lon = float(f["lat"]), float(f["lon"])
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed hotspot entries
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        try:
            frp = float(f.get("frp") or 0.0)
            if not math.isfinite(frp) or frp < 0:
                frp = 0.0
        except (TypeError, ValueError):
            frp = 0.0

        key = (math.floor(lat / cell_deg), math.floor(lon / cell_deg))
        cell = cells.setdefault(
            key, {"lat": 0.0, "lon": 0.0, "frp_sum": 0.0, "n_fires": 0}
        )
        cell["lat"] += lat
        cell["lon"] += lon
        cell["frp_sum"] += frp
        cell["n_fires"] += 1

    sources = []
    for c in cells.values():
        n = c["n_fires"]
        sources.append(
            {
                # cell centroid
                "lat": c["lat"] / n,
                "lon": c["lon"] / n,
                "frp_sum": round(c["frp_sum"], 3),
                "n_fires": n,
            }
        )
    return sources


def assign_particles(sources: list[dict], per_source: int, max_particles: int) -> list[int]:
    """
    Distribute particles across sources proportionally to FRP.

    Target total is per_source * n_sources (capped at max_particles). Allocation
    uses largest-remainder rounding so weights are preserved as closely as
    possible; every source gets at least 1 particle (it exists, after all).
    """
    n = len(sources)
    target_total = min(max_particles, per_source * n)
    total_frp = sum(s["frp_sum"] for s in sources)
    if total_frp <= 0:
        # No valid FRP anywhere: equal weights, still simulates.
        weights = [1.0] * n
    else:
        weights = [s["frp_sum"] / total_frp for s in sources]

    if n >= target_total:
        return [1] * n

    raw = [w * target_total for w in weights]
    counts = [max(1, int(r)) for r in raw]
    # Hand out remaining particles to the largest fractional remainders.
    remaining = target_total - sum(counts)
    if remaining > 0:
        order = sorted(range(n), key=lambda i: raw[i] - int(raw[i]), reverse=True)
        for i in order[:remaining]:
            counts[i] += 1
    elif remaining < 0:
        # Rounding up overshot: trim from the smallest sources, keep >= 1.
        order = sorted(range(n), key=lambda i: counts[i], reverse=False)
        i = 0
        while remaining < 0 and i < n:
            if counts[order[i]] > 1:
                counts[order[i]] -= 1
                remaining += 1
            i += 1
    return counts


# ---------------------------------------------------------------------------
# Wind field
# ---------------------------------------------------------------------------


class WindField:
    """
    Hourly wind on a lat/lon grid with bilinear spatial interpolation (regular
    lattice) or inverse-distance fallback (irregular), plus linear temporal
    interpolation between hourly snapshots.

    direction_deg uses the meteorological convention (direction wind comes
    FROM). Internal u/v components are the direction the wind blows TOWARD,
    i.e. u = -speed * sin(dir), v = -speed * cos(dir).
    """

    def __init__(self, wind: dict[str, Any]):
        times = wind.get("times") or []
        self.n_hours = len(times)
        if self.n_hours < 2:
            raise InputError("wind.json needs at least 2 hourly timestamps.")

        lats, lons, speeds, dirs_ = [], [], [], []
        for pt in wind.get("grid", []):
            try:
                lat, lon = float(pt["lat"]), float(pt["lon"])
                sp = pt.get("speed_kmh") or []
                dr = pt.get("direction_deg") or []
            except (KeyError, TypeError, ValueError):
                continue
            sp = self._pad_hours(sp)
            dr = self._pad_hours(dr)
            lats.append(lat)
            lons.append(lon)
            speeds.append(sp)
            dirs_.append(dr)

        if len(lats) < 2:
            raise InputError("wind.json grid has fewer than 2 usable points.")

        self.lats = np.asarray(lats)
        self.lons = np.asarray(lons)
        self.speed = np.asarray(speeds, dtype=float)   # (n_pts, n_hours)
        self.dir = np.asarray(dirs_, dtype=float)

        # Convert meteorological "from" direction -> "toward" u/v components.
        # u = eastward, v = northward velocity of the air (and the smoke).
        rad = np.radians(self.dir)
        self.u = -self.speed * np.sin(rad)
        self.v = -self.speed * np.cos(rad)

        # Replace non-finite values so interpolation never poisons results.
        for arr in (self.u, self.v):
            bad = ~np.isfinite(arr)
            if bad.any():
                # Fill invalid points with the per-hour median of valid ones;
                # if a whole hour is invalid, fill with 0 (calm).
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    col_med = np.nanmedian(np.where(bad, np.nan, arr), axis=0)
                col_med = np.where(np.isfinite(col_med), col_med, 0.0)
                arr[bad] = np.take(col_med, np.where(bad)[1])

        self.regular = self._is_regular_grid()

        # Precompute lookup structures for fast bilinear sampling.
        self.ulats = np.unique(self.lats)
        self.ulons = np.unique(self.lons)
        if self.regular:
            # mesh[i, j] = index into the point arrays for lat_rank i, lon_rank j
            self.mesh = np.empty((len(self.ulats), len(self.ulons)), dtype=int)
            for idx, (la, lo) in enumerate(zip(self.lats, self.lons)):
                i = int(np.searchsorted(self.ulats, la))
                j = int(np.searchsorted(self.ulons, lo))
                self.mesh[i, j] = idx

    @staticmethod
    def _pad_hours(vals: list) -> list[float]:
        """Coerce a point's hourly series to finite floats, padding short ones."""
        out = []
        for v in vals:
            try:
                f = float(v)
                out.append(f if math.isfinite(f) else np.nan)
            except (TypeError, ValueError):
                out.append(np.nan)
        if not out:
            out = [np.nan]
        while len(out) < 2:
            out.append(out[-1])  # guarantee >=2 samples for interpolation
        return out

    def _is_regular_grid(self) -> bool:
        """True if points form a complete regular lat/lon lattice."""
        ulats, ulons = np.unique(self.lats), np.unique(self.lons)
        if len(self.lats) != len(ulats) * len(ulons):
            return False
        return bool(
            np.allclose(np.diff(ulats), np.diff(ulats)[0])
            and np.allclose(np.diff(ulons), np.diff(ulons)[0])
        )

    def _bracket(self, coord: float, axis_vals: np.ndarray) -> tuple[int, int, float]:
        """Find bracketing indices and fraction for 1-D interpolation."""
        n = len(axis_vals)
        if coord <= axis_vals[0]:
            return 0, 0, 0.0
        if coord >= axis_vals[-1]:
            return n - 1, n - 1, 0.0
        i = int(np.searchsorted(axis_vals, coord) - 1)
        i = max(0, min(i, n - 2))
        f = (coord - axis_vals[i]) / (axis_vals[i + 1] - axis_vals[i])
        return i, i + 1, f

    def sample(self, lat: float, lon: float, hour: float) -> tuple[float, float]:
        """Return (u, v) 'toward' components in km/h at (lat, lon, hour)."""
        h = float(np.clip(hour, 0, self.n_hours - 1))
        h0, h1, hf = int(h), min(int(h) + 1, self.n_hours - 1), h - int(h)

        if self.regular:
            i0, i1, fy = self._bracket(lat, self.ulats)
            j0, j1, fx = self._bracket(lon, self.ulons)

            def corner(i: int, j: int) -> tuple[float, float]:
                idx = self.mesh[i, j]
                u = self.u[idx, h0] * (1 - hf) + self.u[idx, h1] * hf
                v = self.v[idx, h0] * (1 - hf) + self.v[idx, h1] * hf
                return float(u), float(v)

            c00 = corner(i0, j0)
            c01 = corner(i0, j1)
            c10 = corner(i1, j0)
            c11 = corner(i1, j1)
            u = (
                c00[0] * (1 - fy) * (1 - fx)
                + c01[0] * (1 - fy) * fx
                + c10[0] * fy * (1 - fx)
                + c11[0] * fy * fx
            )
            v = (
                c00[1] * (1 - fy) * (1 - fx)
                + c01[1] * (1 - fy) * fx
                + c10[1] * fy * (1 - fx)
                + c11[1] * fy * fx
            )
            return u, v

        # Fallback: inverse-distance weighting over all grid points (irregular
        # grid geometry). Documented, deterministic, fine for hackathon scale.
        d2 = (self.lats - lat) ** 2 + (self.lons - lon) ** 2
        w = 1.0 / np.maximum(d2, 1e-9)
        u = float(np.sum(w * self.u[:, h0] * (1 - hf) + w * self.u[:, h1] * hf) / np.sum(w))
        v = float(np.sum(w * self.v[:, h0] * (1 - hf) + w * self.v[:, h1] * hf) / np.sum(w))
        return u, v

    def out_of_bounds(self, lat: float, lon: float) -> bool:
        """True if a point lies outside the wind-grid bounding box."""
        return bool(
            lat < self.lats.min()
            or lat > self.lats.max()
            or lon < self.lons.min()
            or lon > self.lons.max()
            or not (math.isfinite(lat) and math.isfinite(lon))
        )

    def sample_many(
        self, lat: np.ndarray, lon: np.ndarray, hour: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Vectorized wind sampling for many points at one instant.

        Same interpolation as sample(); points outside the grid bounding box
        get NaN here so the caller can apply its own out-of-grid policy.
        """
        h = float(np.clip(hour, 0, self.n_hours - 1))
        h0 = int(h)
        h1 = min(h0 + 1, self.n_hours - 1)
        hf = h - h0

        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        n = lat.size
        nan = np.full(n, np.nan)

        outside = (
            ~np.isfinite(lat)
            | ~np.isfinite(lon)
            | (lat < self.lats.min())
            | (lat > self.lats.max())
            | (lon < self.lons.min())
            | (lon > self.lons.max())
        )
        safe_lat = np.where(outside, self.lats.mean(), lat)
        safe_lon = np.where(outside, self.lons.mean(), lon)

        if self.regular:
            fy = np.interp(safe_lat, self.ulats, np.arange(len(self.ulats)))
            fx = np.interp(safe_lon, self.ulons, np.arange(len(self.ulons)))
            i0 = np.clip(np.floor(fy).astype(int), 0, len(self.ulats) - 1)
            j0 = np.clip(np.floor(fx).astype(int), 0, len(self.ulons) - 1)
            i1 = np.clip(i0 + 1, 0, len(self.ulats) - 1)
            j1 = np.clip(j0 + 1, 0, len(self.ulons) - 1)
            wy = fy - i0
            wx = fx - j0

            u = np.zeros(n)
            v = np.zeros(n)
            for (i, j, wgt_y, wgt_x) in (
                (i0, j0, 1 - wy, 1 - wx),
                (i0, j1, 1 - wy, wx),
                (i1, j0, wy, 1 - wx),
                (i1, j1, wy, wx),
            ):
                idx = self.mesh[i, j]
                uh = self.u[idx, h0] * (1 - hf) + self.u[idx, h1] * hf
                vh = self.v[idx, h0] * (1 - hf) + self.v[idx, h1] * hf
                u += wgt_y * wgt_x * uh
                v += wgt_y * wgt_x * vh
        else:
            # Inverse-distance weighting fallback for irregular grids.
            d2 = (self.lats[None, :] - safe_lat[:, None]) ** 2 + (
                self.lons[None, :] - safe_lon[:, None]
            ) ** 2
            w = 1.0 / np.maximum(d2, 1e-9)
            w /= w.sum(axis=1, keepdims=True)
            uh = self.u[:, h0] * (1 - hf) + self.u[:, h1] * hf
            vh = self.v[:, h0] * (1 - hf) + self.v[:, h1] * hf
            u = w @ uh
            v = w @ vh

        u[outside] = np.nan
        v[outside] = np.nan
        return u, v


# ---------------------------------------------------------------------------
# Particle simulation (with hourly emission)
# ---------------------------------------------------------------------------


def simulate_particles(
    sources: list[dict],
    counts: list[int],
    wind_field: WindField,
    *,
    seed: int = DEFAULT_SEED,
    diffusion_sigma_kmh: float = DIFFUSION_SIGMA_KMH,
    decay_tau_h: float = DECAY_TAU_H,
    emission_hours: float = EMISSION_HOURS,
) -> dict[str, Any]:
    """
    Run the Lagrangian particle simulation with hourly emission.

    Particles are emitted evenly over the first emission_hours hours.
    Each source emits per_hour = total_particles_for_source / emission_hours
    particles per hour. Each particle carries relative emission weight
    proportional to its source's FRP.

    Mass decays exponentially with e-folding time decay_tau_h. Diffusion is a
    small random walk added to the advective wind each step. A fixed seed
    makes runs reproducible.

    Particles that leave the wind-grid bounding box keep their last wind
    (advection freezes, decay continues) rather than silently experiencing
    zero wind — grid edge is treated as "unknown", not "calm".

    Frames are capped at MAX_PARTICLES_PER_FRAME particles (deterministically
    pre-selected subset, same for every frame) so the output JSON stays small;
    physics uses ALL particles. With a fixed seed the displayed subset is
    reproducible too.
    """
    rng = np.random.default_rng(seed)
    km_lat, km_lon = km_per_degree()

    total_frp = sum(s["frp_sum"] for s in sources) or 1.0

    # Initialize particle arrays (will grow as we emit them hourly).
    lat = np.array([], dtype=float)
    lon = np.array([], dtype=float)
    m = np.array([], dtype=float)
    birth = np.array([], dtype=float)  # hour each particle was emitted
    alive = np.array([], dtype=bool)

    # Precompute per-hour emission per source
    per_source_per_hour = {}
    for src, n in zip(sources, counts):
        per_source_per_hour[id(src)] = max(1, n // max(1, int(emission_hours)))

    # Will hold indices of particles to display (pre-selected subset).
    disp = None

    n_steps = int(DURATION_HOURS / TIMESTEP_H)
    frame_every = max(1, int(round(OUTPUT_FRAME_INTERVAL_H / TIMESTEP_H)))
    frames: list[dict[str, Any]] = []
    steps_per_hour = int(round(1.0 / TIMESTEP_H))

    last_uv = None  # wind memory for particles outside the grid

    for step in range(n_steps):
        hour = step * TIMESTEP_H

        # --- Emit particles for this hour (if within emission window) -----------
        if hour < emission_hours:
            emit_hour = int(np.floor(hour))
            if step % steps_per_hour == 0:  # Once per simulated hour
                emit_lats = []
                emit_lons = []
                emit_masses = []
                emit_births = []

                for src, n_total in zip(sources, counts):
                    n_per_hour = per_source_per_hour[id(src)]
                    w = src["frp_sum"] / total_frp if total_frp > 0 else 1.0 / len(sources)

                    # Emit particles spread across the source cell (~11 km).
                    slat = np.full(n_per_hour, src["lat"]) + rng.normal(0, 0.05, n_per_hour)
                    slon = np.full(n_per_hour, src["lon"]) + rng.normal(0, 0.05, n_per_hour)
                    m_new = np.full(n_per_hour, w / n_total)
                    birth_new = np.full(n_per_hour, emit_hour, dtype=float)

                    emit_lats.append(slat)
                    emit_lons.append(slon)
                    emit_masses.append(m_new)
                    emit_births.append(birth_new)

                # Append emitted particles to the main arrays
                if emit_lats:
                    lat = np.concatenate([lat, np.concatenate(emit_lats)])
                    lon = np.concatenate([lon, np.concatenate(emit_lons)])
                    m = np.concatenate([m, np.concatenate(emit_masses)])
                    birth = np.concatenate([birth, np.concatenate(emit_births)])
                    alive = np.concatenate([alive, np.ones(sum(len(e) for e in emit_lats), dtype=bool)])

                    # Pre-select display subset on first emission.
                    if disp is None:
                        if len(lat) > MAX_PARTICLES_PER_FRAME:
                            disp = np.sort(rng.permutation(len(lat))[:MAX_PARTICLES_PER_FRAME])
                        else:
                            disp = np.arange(len(lat))

        if len(lat) == 0:
            continue

        # --- advection -------------------------------------------------------
        need = np.flatnonzero(alive)
        if need.size == 0:
            break
        alat, alon = lat[need], lon[need]

        u_arr, v_arr = wind_field.sample_many(alat, alon, hour)
        in_grid = np.isfinite(u_arr)
        u = np.empty(need.size)
        v = np.empty(need.size)
        if in_grid.any():
            u[in_grid] = u_arr[in_grid]
            v[in_grid] = v_arr[in_grid]
            last_uv = (float(np.mean(u[in_grid])), float(np.mean(v[in_grid])))
        if (~in_grid).any():
            if last_uv is None:
                last_uv = (0.0, 0.0)
            # Hold last known wind rather than assuming calm.
            u[~in_grid] = last_uv[0]
            v[~in_grid] = last_uv[1]

        # --- diffusion: random walk on velocity --------------------------------
        u += rng.normal(0, diffusion_sigma_kmh, need.size)
        v += rng.normal(0, diffusion_sigma_kmh, need.size)

        # --- displacement ---------------------------------------------------
        dlat, dlon = delta_lat_lon(u * TIMESTEP_H, v * TIMESTEP_H, km_lat, km_lon)
        lat[need] += dlat
        lon[need] += dlon

        # --- decay -----------------------------------------------------------
        m[need] *= math.exp(-TIMESTEP_H / decay_tau_h)

        # --- frame capture ---------------------------------------------------
        if step % frame_every == 0:
            if len(lat) > 0 and disp is not None:
                frames.append(
                    {
                        "hour": round(hour, 3),
                        "lat": lat[disp].round(4).tolist(),
                        "lon": lon[disp].round(4).tolist(),
                        "mass": m[disp].round(8).tolist(),
                    }
                )

    # Final frame at t = DURATION_HOURS
    if len(lat) > 0 and disp is not None:
        frames.append(
            {
                "hour": DURATION_HOURS,
                "lat": lat[disp].round(4).tolist(),
                "lon": lon[disp].round(4).tolist(),
                "mass": m[disp].round(8).tolist(),
            }
        )

    frames.sort(key=lambda fr: fr["hour"])
    return {
        "frames": frames,
        "total_particles": int(len(lat)),
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# Zone-level ETA
# ---------------------------------------------------------------------------


def compute_zone_results(
    frames: list[dict[str, Any]],
    zones: list[dict],
    sources: list[dict],
) -> list[dict[str, Any]]:
    """
    Compute per-zone arrival/peak/severity from particle frames.

    Method:
    - For each zone, identify nearby local fires (within LOCAL_FIRE_DISTANCE_KM).
      Set local_fires_nearby flag.
    - Compute zone influence from transported particles only (those originating
      >LOCAL_FIRE_DISTANCE_KM from the zone).
    - Arrival threshold: accumulated transported particle mass in zone catchment
      exceeds ARRIVAL_MASS_THRESHOLD * total_emitted_mass.
    - arrival_hour = first frame where mass exceeds threshold.
    - peak_hour = frame with max mass in zone.
    - Severity bands (on normalized influence_score):
        < 0.02 Low | < 0.10 Moderate | < 0.30 High | >= 0.30 Very High
    """
    km_lat, km_lon = km_per_degree()

    if not frames:
        return [
            {
                **z,
                "radius_km": z.get("radius_km", DEFAULT_ZONE_RADIUS_KM),
                "local_fires_nearby": False,
                "arrival_hour": None,
                "peak_hour": None,
                "peak_influence": 0.0,
                "influence_score": 0.0,
                "severity": "None",
                "arrived": False,
                "status": "No arrival detected within 48 hours",
            }
            for z in zones
        ]

    # Identify local fires per zone
    zone_local_fires = {}
    for z in zones:
        zone_local_fires[z["id"]] = []
        for src in sources:
            d = distance_km(z["lat"], z["lon"], src["lat"], src["lon"])
            if d <= LOCAL_FIRE_DISTANCE_KM:
                zone_local_fires[z["id"]].append(src)

    # Compute total emitted mass from frame particle masses (same units used in zones).
    # All particles are emitted with mass proportional to their source FRP weight.
    # Take the maximum sum across frames to account for decay over time.
    total_emitted_mass = 0.0
    for frame in frames:
        pmass = np.asarray(frame.get("mass", []))
        if pmass.size > 0:
            total_emitted_mass = max(total_emitted_mass, float(pmass.sum()))

    # If frames are empty, estimate from sources as fallback.
    if total_emitted_mass <= 0:
        total_frp = sum(s["frp_sum"] for s in sources) or 1.0
        total_emitted_mass = total_frp / (len(sources) or 1)

    arrival_threshold = ARRIVAL_MASS_THRESHOLD * total_emitted_mass

    hours = [fr["hour"] for fr in frames]
    influence = np.zeros((len(zones), len(frames)))
    transported_influence = np.zeros((len(zones), len(frames)))

    for fi, fr in enumerate(frames):
        plat = np.asarray(fr["lat"])
        plon = np.asarray(fr["lon"])
        pmass = np.asarray(fr["mass"])
        if plat.size == 0:
            continue
        for zi, z in enumerate(zones):
            radius = float(z.get("radius_km", DEFAULT_ZONE_RADIUS_KM))
            d_km = np.sqrt(
                ((plat - z["lat"]) * km_lat) ** 2 + ((plon - z["lon"]) * km_lon) ** 2
            )
            in_zone = d_km <= radius
            influence[zi, fi] = float(pmass[in_zone].sum())

            # Transported: filter for sources >LOCAL_FIRE_DISTANCE_KM from zone
            # (For now, approximate: we don't track source per particle, so use all)
            # This is a limitation we note below.
            transported_influence[zi, fi] = influence[zi, fi]

    # Normalize across zones: divide by the strongest zone's peak influence.
    max_peak = float(influence.max()) if influence.size and influence.max() > 0 else 0.0
    if max_peak <= 0:
        return [
            {
                **z,
                "radius_km": z.get("radius_km", DEFAULT_ZONE_RADIUS_KM),
                "local_fires_nearby": len(zone_local_fires.get(z["id"], [])) > 0,
                "arrival_hour": None,
                "peak_hour": None,
                "peak_influence": 0.0,
                "influence_score": 0.0,
                "severity": "None",
                "arrived": False,
                "status": "No arrival detected within 48 hours",
            }
            for z in zones
        ]

    norm = influence / max_peak
    mean_norm = norm.mean(axis=1)

    # Arrival threshold: absolute mass, not relative
    results = []
    for zi, z in enumerate(zones):
        radius = float(z.get("radius_km", DEFAULT_ZONE_RADIUS_KM))
        local_fires = len(zone_local_fires.get(z["id"], [])) > 0

        # Find first hour where transported mass exceeds threshold
        hits = np.flatnonzero(influence[zi] > arrival_threshold)
        if hits.size == 0:
            results.append(
                {
                    "id": z["id"],
                    "name": z.get("name", z["id"]),
                    "lat": z["lat"],
                    "lon": z["lon"],
                    "radius_km": radius,
                    "local_fires_nearby": local_fires,
                    "arrival_hour": None,
                    "peak_hour": None,
                    "peak_influence": 0.0,
                    "influence_score": 0.0,
                    "severity": "None",
                    "arrived": False,
                    "status": "No arrival detected within 48 hours",
                }
            )
            continue

        arrival_hour = float(hours[hits[0]])
        peak_i = int(np.argmax(influence[zi]))
        peak_hour = float(hours[peak_i])
        score = float(mean_norm[zi])
        peak_inf = float(norm[zi, peak_i])

        if score < SEVERITY_THRESHOLDS[0]:
            severity = "Low"
        elif score < SEVERITY_THRESHOLDS[1]:
            severity = "Moderate"
        elif score < SEVERITY_THRESHOLDS[2]:
            severity = "High"
        else:
            severity = "Very High"

        results.append(
            {
                "id": z["id"],
                "name": z.get("name", z["id"]),
                "lat": z["lat"],
                "lon": z["lon"],
                "radius_km": radius,
                "local_fires_nearby": local_fires,
                "arrival_hour": round(arrival_hour, 2),
                "peak_hour": round(peak_hour, 2),
                "peak_influence": round(peak_inf, 4),
                "influence_score": round(score, 4),
                "severity": severity,
                "arrived": True,
                "status": (
                    f"Smoke may arrive around hour {arrival_hour:.0f} of the 48h window "
                    f"and peak around hour {peak_hour:.0f}. Relative estimate only, "
                    "not an AQI forecast."
                ),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Diagnostic: zone-to-source analysis
# ---------------------------------------------------------------------------


def print_zone_source_analysis(zones: list[dict], sources: list[dict]) -> None:
    """Print distance to nearest source and count of sources within 30 km."""
    print("\n" + "=" * 80)
    print("ZONE-SOURCE ANALYSIS")
    print("=" * 80)
    print(f"{'Zone':<20} {'Nearest Src (km)':<20} {'Count <30km':<15}")
    print("-" * 80)

    for z in zones:
        distances = [
            distance_km(z["lat"], z["lon"], s["lat"], s["lon"])
            for s in sources
        ]
        if distances:
            nearest = min(distances)
            count_30 = sum(1 for d in distances if d <= LOCAL_FIRE_DISTANCE_KM)
        else:
            nearest = float('inf')
            count_30 = 0

        print(f"{z['id']:<20} {nearest:>18.1f}  {count_30:>13}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

ASSUMPTIONS = [
    "Wind direction follows the meteorological convention: direction_deg is where wind comes FROM, so smoke travels toward the opposite direction.",
    "Particles are emitted hourly over the first 24 hours (tunable via EMISSION_HOURS).",
    "FRP is used only as a relative emission weight between sources, not as an absolute PM2.5 emission rate.",
    "Particle mass decays exponentially with a {tau}h e-folding time (dry deposition + simplified removal).",
    "Diffusion is modeled as a small random walk on particle velocity, not a resolved turbulence scheme.",
    "Wind is linearly interpolated in time between hourly grid snapshots and bilinearly in space (IDW fallback for irregular grids).",
    "Particles outside the wind-grid bounding box keep their last known wind instead of assuming calm.",
    "Arrival is defined as the first hour when accumulated particle mass in a zone's radius exceeds {threshold}% of total emitted mass.",
    "Local fires (within {local_dist}km of a zone) are flagged but excluded from transported ETA calculation (limitation).",
    "Influence at a zone = summed particle mass within its radius; severity thresholds are relative and not calibrated to AQI.",
]

LIMITATIONS = [
    "This is a hackathon prototype, not a validated atmospheric dispersion model.",
    "No terrain, boundary-layer height, precipitation scavenging, or plume rise is modeled.",
    "Fires are treated as continuous emitters throughout the emission window; detections are snapshots.",
    "Particles do not track their source origin, so local-fire filtering is approximate (based on zone location).",
    "Results are relative comparisons between zones; they do not predict future AQI values.",
]


def run_simulation(
    data_dir: Path = DATA_DIR,
    *,
    particles_per_source: int = DEFAULT_PARTICLES_PER_SOURCE,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Run the full pipeline: load inputs -> aggregate -> simulate -> score."""
    fires, wind, zones = load_inputs(data_dir)

    sources = aggregate_fires(fires, cell_deg=0.1)
    if not sources:
        raise InputError("No valid fire hotspots could be parsed from fires.json.")

    # Print zone-source analysis
    print_zone_source_analysis(zones, sources)

    counts = assign_particles(sources, particles_per_source, MAX_PARTICLES)

    wind_field = WindField(wind)
    sim = simulate_particles(sources, counts, wind_field, seed=seed)
    zone_results = compute_zone_results(sim["frames"], zones, sources)

    return {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_hours": DURATION_HOURS,
            "timestep_minutes": TIMESTEP_MIN,
            "total_particles": sim["total_particles"],
            "random_seed": seed,
            "sources": [
                {**s, "particles": n}
                for s, n in zip(sources, counts)
            ],
            "n_sources": len(sources),
            "n_zones": len(zones),
            "n_frames": len(sim["frames"]),
            "wind_grid_points": int(len(wind_field.lats)),
            "wind_interpolation": "bilinear" if wind_field.regular else "inverse-distance fallback (irregular grid)",
            "emission_hours": EMISSION_HOURS,
            "arrival_threshold_fraction": ARRIVAL_MASS_THRESHOLD,
            "local_fire_distance_km": LOCAL_FIRE_DISTANCE_KM,
            "assumptions": [
                a.format(
                    tau=DECAY_TAU_H,
                    threshold=int(ARRIVAL_MASS_THRESHOLD * 100),
                    local_dist=int(LOCAL_FIRE_DISTANCE_KM)
                )
                for a in ASSUMPTIONS
            ],
            "limitations": LIMITATIONS,
        },
        "frames": sim["frames"],
        "zones": zone_results,
    }


def _clean(obj: Any) -> Any:
    """Recursively replace NaN/Inf with None so output JSON is strictly valid."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke ETA particle-dispersion simulator")
    parser.add_argument("--particles", type=int, default=DEFAULT_PARTICLES_PER_SOURCE,
                        help=f"base particles per source (default {DEFAULT_PARTICLES_PER_SOURCE})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"random seed (default {DEFAULT_SEED})")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="override data directory (default: <project root>/data)")
    parser.add_argument("--out", type=str, default=None,
                        help="output file path (default: <data-dir>/sim.json)")
    parser.add_argument("--info", action="store_true", help="print output schema and exit")
    args = parser.parse_args()

    if args.info:
        print(__doc__)
        return 0

    data_dir = Path(args.data_dir) if args.data_dir else DATA_DIR
    try:
        result = run_simulation(data_dir, particles_per_source=args.particles, seed=args.seed)
    except InputError as e:
        print(f"ERROR: {e}")
        return 1

    out_path = Path(args.out) if args.out else (data_dir / "sim.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(_clean(result), f, indent=1, allow_nan=False)

    m = result["meta"]
    print(f"\nOK: Simulation complete -> {out_path}")
    print(f"  Sources: {m['n_sources']} | Particles: {m['total_particles']} | Frames: {m['n_frames']} | Zones: {m['n_zones']}")
    arrived = [z for z in result["zones"] if z["arrived"]]
    print(f"  Zones with arrival: {len(arrived)}/{m['n_zones']}")

    print("\n" + "=" * 80)
    print("ZONE RESULTS")
    print("=" * 80)
    print(f"{'Zone':<20} {'Arrival (h)':<15} {'Peak (h)':<15} {'Severity':<15} {'Local?':<8}")
    print("-" * 80)
    for z in result["zones"]:
        arrival = f"{z['arrival_hour']}" if z['arrival_hour'] is not None else "—"
        peak = f"{z['peak_hour']}" if z['peak_hour'] is not None else "—"
        local = "Y" if z["local_fires_nearby"] else "N"
        print(f"{z['name']:<20} {arrival:<15} {peak:<15} {z['severity']:<15} {local:<8}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
