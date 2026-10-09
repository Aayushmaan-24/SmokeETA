#!/usr/bin/env python3
"""
Real replay date scanner: query FIRMS and Open-Meteo archives to find dates
with high fire activity and favorable wind direction toward Delhi.

Dates scanned:
  - 2025-10-20 to 2025-11-20
  - 2024-10-20 to 2024-11-20

Criteria for favorable date:
  - Fire count in belt (lat 29-32, lon 74-77.5) > 0
  - Wind-from direction 250-350° (W to N, blowing toward SE)
  - Mean wind speed >= 8 km/h

Results sorted by (belt_fires × favorable).

All API responses cached under data/replay/_scan_cache/ for instant reruns.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

CACHE_DIR = PROJECT_ROOT / "data" / "replay" / "_scan_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CLIENT = httpx.Client(timeout=30.0, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))


def circular_mean(angles_deg):
    """Compute circular mean of angles in degrees."""
    if not angles_deg:
        return 0.0
    angles_rad = np.radians(angles_deg)
    sin_sum = np.sum(np.sin(angles_rad))
    cos_sum = np.sum(np.cos(angles_rad))
    mean_rad = np.arctan2(sin_sum, cos_sum)
    mean_deg = np.degrees(mean_rad)
    return mean_deg % 360


def cache_key(endpoint: str, params: dict) -> str:
    """Generate cache key from endpoint and params."""
    key_str = endpoint + json.dumps(params, sort_keys=True)
    return hashlib.md5(key_str.encode()).hexdigest()


def fetch_cached(endpoint: str, params: dict, label: str = "") -> str | None:
    """Fetch from API with caching."""
    key = cache_key(endpoint, params)
    cache_file = CACHE_DIR / f"{key}.json"

    if cache_file.exists():
        with open(cache_file) as f:
            cached = json.load(f)
            if label:
                print(f"  {label} (cached)", end="", flush=True)
            return cached["response"]

    # Fetch from API
    try:
        resp = CLIENT.get(endpoint, params=params)
        resp.raise_for_status()
        text = resp.text
    except httpx.HTTPError as e:
        print(f"  ERROR: {e}")
        return None

    # Cache
    with open(cache_file, "w") as f:
        json.dump({"response": text, "timestamp": datetime.utcnow().isoformat()}, f)

    if label:
        print(f"  {label}", end="", flush=True)
    return text


def fetch_firms_belt_count(date_str: str) -> int:
    """Fetch FIRMS fires for a single day, count in belt (lat 29-32, lon 74-77.5)."""
    key = os.getenv("FIRMS_KEY")
    if not key:
        print("ERROR: FIRMS_KEY not set in .env")
        return 0

    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_SP/73.5,27.5,78.5,32.5/1/{date_str}"
    params = {}

    text = fetch_cached(url, params, label=f"fires")
    if not text:
        return 0

    # Parse CSV
    try:
        df_lines = text.strip().split('\n')
        if not df_lines or not df_lines[0].startswith('latitude'):
            return 0

        reader = csv.DictReader(StringIO(text))
        fires = []
        for row in reader:
            try:
                lat = float(row['latitude'])
                lon = float(row['longitude'])
                conf = str(row.get('confidence', ''))

                # Filter by belt and confidence
                if 29 <= lat <= 32 and 74 <= lon <= 77.5 and conf in ['n', 'h']:
                    fires.append({'lat': lat, 'lon': lon})
            except (ValueError, KeyError):
                continue

        return len(fires)
    except Exception as e:
        print(f"  ERROR parsing CSV: {e}")
        return 0


def fetch_wind_daily_stats(date_str: str) -> tuple[float, float]:
    """Fetch Open-Meteo wind for 4 points in belt, compute daily mean speed and circular mean direction."""
    belt_points = [
        (30.0, 75.0),
        (30.5, 76.0),
        (29.5, 76.5),
        (29.0, 76.8),
    ]

    lat_list = ",".join(str(pt[0]) for pt in belt_points)
    lon_list = ",".join(str(pt[1]) for pt in belt_points)

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat_list,
        "longitude": lon_list,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "start_date": date_str,
        "end_date": date_str,
        "timezone": "UTC",
    }

    text = fetch_cached(url, params, label=f"wind")
    if not text:
        return 0.0, 0.0

    try:
        data = json.loads(text)
        if not isinstance(data, list):
            data = [data]

        all_speeds = []
        all_directions = []

        for location_data in data:
            hourly = location_data.get('hourly', {})
            speeds = hourly.get('wind_speed_10m', [])
            directions = hourly.get('wind_direction_10m', [])

            all_speeds.extend(speeds)
            all_directions.extend(directions)

        mean_speed = np.mean(all_speeds) if all_speeds else 0.0
        mean_direction = circular_mean(all_directions) if all_directions else 0.0

        return mean_speed, mean_direction
    except Exception as e:
        print(f"  ERROR parsing wind: {e}")
        return 0.0, 0.0


def is_favorable(wind_from_deg: float, mean_speed: float) -> bool:
    """Check if wind direction and speed are favorable for transport toward Delhi."""
    # Wind from 250-350° (W to N) blows toward 70-170° (E to S), hitting SE Delhi
    # Normalize to [0, 360)
    wind_from_deg = wind_from_deg % 360

    favorable_direction = (250 <= wind_from_deg <= 360) or (0 <= wind_from_deg <= 360)
    if 250 <= wind_from_deg or wind_from_deg <= 360:
        favorable_direction = True
    else:
        favorable_direction = False

    favorable_speed = mean_speed >= 8.0
    return favorable_direction and favorable_speed


def scan_dates():
    """Scan all dates and return sorted results."""
    date_ranges = [
        (datetime(2025, 10, 20), datetime(2025, 11, 20)),
        (datetime(2024, 10, 20), datetime(2024, 11, 20)),
    ]

    results = []
    all_dates = set()

    for start_date, end_date in date_ranges:
        current = start_date
        while current <= end_date:
            all_dates.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

    print(f"\nScanning {len(all_dates)} dates for fires and wind...\n")

    for i, date_str in enumerate(sorted(all_dates), 1):
        print(f"[{i:3d}/{len(all_dates)}] {date_str}: ", end="", flush=True)

        belt_fires = fetch_firms_belt_count(date_str)
        print(", ", end="", flush=True)

        mean_speed, wind_from = fetch_wind_daily_stats(date_str)
        print()

        favorable = is_favorable(wind_from, mean_speed)
        score = belt_fires if favorable else 0

        results.append({
            "date": date_str,
            "belt_fires": int(belt_fires),
            "wind_from_deg": float(wind_from),
            "mean_speed_kmh": float(mean_speed),
            "favorable": bool(favorable),
            "score": int(score),
        })

        # Rate limit
        time.sleep(0.1)

    return results


def print_results(results: list[dict]):
    """Print and return top 15 results sorted by score."""
    # Sort by score (favorable first, then by fire count)
    sorted_results = sorted(results, key=lambda r: (r["score"], r["belt_fires"]), reverse=True)

    print("\n" + "=" * 100)
    print("TOP 15 FAVORABLE DATES FOR REPLAY")
    print("=" * 100)
    print(f"{'Date':<12} {'Belt Fires':<12} {'Wind FROM (°)':<15} {'Speed (km/h)':<15} {'Favorable':<12}")
    print("-" * 100)

    for r in sorted_results[:15]:
        fav_str = "✓ YES" if r["favorable"] else "✗ NO"
        print(f"{r['date']:<12} {r['belt_fires']:<12} {r['wind_from_deg']:>6.1f}°        {r['mean_speed_kmh']:>6.1f}         {fav_str:<12}")

    print("=" * 100)
    print(f"\nFavorable = wind FROM 250–350° (W to N) AND speed ≥ 8 km/h")
    print(f"Scanned dates: 2024-10-20 to 2024-11-20 and 2025-10-20 to 2025-11-20")
    print(f"Belt: lat 29–32°, lon 74–77.5°")

    return sorted_results[:15]


def run_replay(replay_date: str):
    """Run full replay pipeline: fetch + simulate."""
    print(f"\n{'=' * 80}")
    print(f"RUNNING REPLAY MODE FOR {replay_date}")
    print(f"{'=' * 80}\n")

    import subprocess

    replay_dir = PROJECT_ROOT / "data" / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Fetch replay data
    print("STEP 1: Fetching replay data (fires, wind, AQI)...")
    result = subprocess.run(
        [sys.executable, "backend/fetch_data.py", "--replay", replay_date],
        cwd=PROJECT_ROOT,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"ERROR: Failed to fetch replay data for {replay_date}")
        return 1

    # Step 2: Run simulation on replay data
    print("\n" + "=" * 80)
    print("STEP 2: Running particle-dispersion simulation on replay data...")
    result = subprocess.run(
        [
            sys.executable,
            "backend/simulate.py",
            "--data-dir", str(replay_dir),
            "--out", str(replay_dir / "sim.json"),
        ],
        cwd=PROJECT_ROOT,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"ERROR: Simulation failed for {replay_date}")
        return 1

    # Step 3: Print results
    print("\n" + "=" * 80)
    print("REPLAY RESULTS")
    print("=" * 80)

    sim_path = replay_dir / "sim.json"
    if sim_path.exists():
        with open(sim_path) as f:
            sim = json.load(f)

        m = sim["meta"]
        print(f"\nGenerated: {m['generated_at']}")
        print(f"Sources: {m['n_sources']} | Particles: {m['total_particles']} | Zones: {m['n_zones']}")

        arrived = [z for z in sim["zones"] if z["arrived"]]
        print(f"Zones with arrival: {len(arrived)}/{m['n_zones']}\n")

        print(f"{'Zone':<20} {'Arrival (h)':<15} {'Peak (h)':<15} {'Severity':<15}")
        print("-" * 80)
        for z in sim["zones"]:
            arrival = f"{z['arrival_hour']:.1f}" if z['arrival_hour'] is not None else "—"
            peak = f"{z['peak_hour']:.1f}" if z['peak_hour'] is not None else "—"
            print(f"{z['name']:<20} {arrival:<15} {peak:<15} {z['severity']:<15}")

        print(f"\n✓ Replay data saved to {replay_dir}/")
        print(f"  Access via API: GET /api/sim?mode=replay")
        return 0
    else:
        print(f"ERROR: Simulation output not found at {sim_path}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Real replay date scanner: find dates with high fires and favorable wind"
    )
    parser.add_argument(
        "--run",
        type=str,
        help="Run replay on specific date (YYYY-MM-DD, e.g., 2025-11-05)",
    )
    args = parser.parse_args()

    if args.run:
        return run_replay(args.run)
    else:
        results = scan_dates()
        top_15 = print_results(results)

        # Save results to file
        results_file = PROJECT_ROOT / "REPLAY_SCAN_RESULTS.json"
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nFull results saved to REPLAY_SCAN_RESULTS.json")

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
