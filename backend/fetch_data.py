#!/usr/bin/env python3
"""
Fetch real-time or replay data for Smoke ETA: FIRMS fires, wind grid, and AQI by zone.
Resolves paths relative to the project root.

Usage:
    python backend/fetch_data.py           # Fetch live data (last 2 days)
    python backend/fetch_data.py --replay 2025-11-01  # Fetch 3-day archive (2025-10-30 to 2025-11-01)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Configure httpx client with timeout and retries
CLIENT = httpx.Client(
    timeout=30.0,
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)


def fetch_fires(replay_date: str | None = None) -> list[dict[str, Any]]:
    """
    Fetch FIRMS VIIRS fires from NASA.
    - Reads FIRMS_KEY from .env
    - Queries last 2 days (live) or 3-day window (replay) over bbox (west,south,east,north): 73.5,27.5,78.5,32.5
    - Filters confidence n (nominal) or h (high)
    - Returns list of {lat, lon, frp, confidence, acq_date, acq_time}

    Args:
        replay_date: If set (YYYY-MM-DD), fetch archive for 3-day window ending on that date.
    """
    key = os.getenv("FIRMS_KEY")
    if not key:
        print("ERROR: FIRMS_KEY not set in .env")
        sys.exit(1)

    if replay_date:
        # Archive: VIIRS_SNPP_SP (Standard Product, 3+ days old)
        # Format: /VIIRS_SNPP_SP/bbox/day_range/start_date
        end = datetime.strptime(replay_date, "%Y-%m-%d")
        start = end - timedelta(days=2)
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_SP/73.5,27.5,78.5,32.5/3/{start.strftime('%Y-%m-%d')}"
        params = {}
        print(f"Fetching replay fires for {start.date()} to {end.date()}...")
        print(f"URL: {url}")
    else:
        # Live: NRT (Near Real Time, last 2 days)
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/73.5,27.5,78.5,32.5/2"
        params = {}
        print("Fetching live fires (last 2 days)...")

    for attempt in range(3):
        try:
            resp = CLIENT.get(url, params=params if params else None)
            resp.raise_for_status()
            break
        except httpx.HTTPError as e:
            if attempt < 2:
                wait = 2 ** attempt
                print(f"Fires fetch attempt {attempt + 1}/3 failed, retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"ERROR: Fires fetch failed after 3 attempts: {e}")
                return []

    text = resp.text.strip()
    # Debug: print first 300 chars
    print(f"Response (first 300 chars): {text[:300]}")

    # Check if response is an error message (usually contains 'error' or lacks CSV header)
    if "error" in text.lower() or not text.startswith("latitude"):
        print(f"ERROR: FIRMS API returned error or invalid response:\n{text[:500]}")
        sys.exit(1)

    # Parse CSV
    from io import StringIO
    df = pd.read_csv(StringIO(text))

    # Filter by confidence (VIIRS uses l/n/h for low/nominal/high)
    df = df[df["confidence"].isin(["n", "h"])]

    # Extract required columns
    fires = []
    for _, row in df.iterrows():
        fires.append({
            "lat": float(row["latitude"]),
            "lon": float(row["longitude"]),
            "frp": float(row.get("frp", 0)) if "frp" in row else None,
            "confidence": str(row["confidence"]),
            "acq_date": str(row["acq_date"]),
            "acq_time": str(row["acq_time"]),
        })

    return fires


def fetch_wind(replay_date: str | None = None) -> dict[str, Any]:
    """
    Build 8x8 lat/lon grid across lat 27.5-32.5, lon 73.5-78.5.
    Query Open-Meteo hourly wind for 2 days (live) or 3-day archive window (replay).

    Returns:
    {
        "times": [ISO strings],
        "grid": [
            {"lat": float, "lon": float, "speed_kmh": [list], "direction_deg": [list]},
            ...
        ]
    }

    Note: direction_deg is where wind comes FROM (meteorological convention).
    """
    lat_min, lat_max = 27.5, 32.5
    lon_min, lon_max = 73.5, 78.5

    lats = np.linspace(lat_min, lat_max, 8)
    lons = np.linspace(lon_min, lon_max, 8)
    grid_points = [(lat, lon) for lat in lats for lon in lons]  # 64 points

    if replay_date:
        # Historical archive: 3-day window
        end = datetime.strptime(replay_date, "%Y-%m-%d")
        start = end - timedelta(days=2)
        url = "https://archive-api.open-meteo.com/v1/archive"
        print(f"Fetching historical wind for {start.date()} to {end.date()}...")
    else:
        # Live forecast: next 2 days
        url = "https://api.open-meteo.com/v1/forecast"
        print(f"Fetching live wind forecast (2 days)...")

    all_data = {}
    times = None

    # Batch requests in groups of 16
    batch_size = 16
    for batch_idx in range(0, len(grid_points), batch_size):
        batch = grid_points[batch_idx:batch_idx + batch_size]
        lat_list = ",".join(str(pt[0]) for pt in batch)
        lon_list = ",".join(str(pt[1]) for pt in batch)

        params = {
            "latitude": lat_list,
            "longitude": lon_list,
            "hourly": "wind_speed_10m,wind_direction_10m",
            "timezone": "UTC",
        }

        if replay_date:
            params["start_date"] = (end - timedelta(days=2)).strftime("%Y-%m-%d")
            params["end_date"] = end.strftime("%Y-%m-%d")
        else:
            params["forecast_days"] = 2

        for attempt in range(3):
            try:
                resp = CLIENT.get(url, params=params)
                resp.raise_for_status()
                break
            except httpx.HTTPError as e:
                if attempt < 2:
                    wait = 2 ** attempt
                    print(f"Wind batch {batch_idx // batch_size + 1} attempt {attempt + 1}/3 failed, retrying in {wait}s: {e}")
                    time.sleep(wait)
                else:
                    print(f"WARNING: Wind batch {batch_idx // batch_size + 1} failed after 3 attempts, skipping")
                    continue

        if resp.status_code != 200:
            print(f"WARNING: Wind batch {batch_idx // batch_size + 1} returned status {resp.status_code}, skipping")
            continue

        data = resp.json()

        # Open-Meteo returns a list when multiple locations are queried
        if not isinstance(data, list):
            data = [data]

        # Extract times from first response (same for all)
        if times is None and data:
            times = data[0].get("hourly", {}).get("time", [])

        # Store wind data for each point
        for point_idx, point in enumerate(batch):
            if point_idx < len(data):
                point_data = data[point_idx].get("hourly", {})
                speeds = point_data.get("wind_speed_10m", [])
                directions = point_data.get("wind_direction_10m", [])

                all_data[point] = {
                    "speed_kmh": [float(s) for s in speeds] if speeds else [],
                    "direction_deg": [float(d) for d in directions] if directions else [],
                }

        # Sleep between batches
        if batch_idx + batch_size < len(grid_points):
            time.sleep(0.5)

    # Format output
    grid_list = []
    for lat, lon in grid_points:
        entry = {
            "lat": float(lat),
            "lon": float(lon),
        }
        if (lat, lon) in all_data:
            entry.update(all_data[(lat, lon)])
        else:
            entry["speed_kmh"] = []
            entry["direction_deg"] = []
        grid_list.append(entry)

    return {
        "times": times or [],
        "grid": grid_list,
    }


def fetch_aqi(replay_date: str | None = None) -> dict[str, dict[str, Any]]:
    """
    Query Open-Meteo air-quality API for all zones in zones.json.
    One multi-location request with current PM2.5, PM10, US AQI and hourly data.

    Returns:
    {
        zone_id: {
            "current": {"pm2_5": float, "pm10": float, "us_aqi": int},
            "hourly": {"times": [ISO], "pm2_5": [float], "us_aqi": [int]}
        },
        ...
    }
    """
    zones_path = PROJECT_ROOT / "data" / "zones.json"
    with open(zones_path) as f:
        zones = json.load(f)

    lat_list = ",".join(str(z["lat"]) for z in zones)
    lon_list = ",".join(str(z["lon"]) for z in zones)

    if replay_date:
        # Historical archive
        end = datetime.strptime(replay_date, "%Y-%m-%d")
        start = end - timedelta(days=2)
        url = "https://archive-api.open-meteo.com/v1/archive"
        print(f"Fetching historical AQI for {start.date()} to {end.date()}...")
        params = {
            "latitude": lat_list,
            "longitude": lon_list,
            "hourly": "pm2_5,us_aqi",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            "timezone": "UTC",
        }
    else:
        # Live forecast
        url = "https://air-quality-api.open-meteo.com/v1/air-quality"
        print(f"Fetching live AQI...")
        params = {
            "latitude": lat_list,
            "longitude": lon_list,
            "current": "pm2_5,pm10,us_aqi",
            "hourly": "pm2_5,us_aqi",
            "forecast_days": 2,
            "timezone": "UTC",
        }

    for attempt in range(3):
        try:
            resp = CLIENT.get(url, params=params)
            resp.raise_for_status()
            break
        except httpx.HTTPError as e:
            if attempt < 2:
                wait = 2 ** attempt
                print(f"AQI fetch attempt {attempt + 1}/3 failed, retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"WARNING: AQI fetch failed after 3 attempts")
                return {}

    if resp.status_code != 200:
        print(f"WARNING: AQI API returned status {resp.status_code}")
        return {}

    data = resp.json()

    # Open-Meteo returns a list when multiple locations are queried
    if not isinstance(data, list):
        data = [data]

    aqi_by_zone = {}
    for zone_idx, zone in enumerate(zones):
        if zone_idx < len(data):
            zone_data = data[zone_idx]
            current = zone_data.get("current", {})
            hourly = zone_data.get("hourly", {})

            aqi_by_zone[zone["id"]] = {
                "current": {
                    "pm2_5": float(current.get("pm2_5", 0)) if current.get("pm2_5") is not None else None,
                    "pm10": float(current.get("pm10", 0)) if current.get("pm10") is not None else None,
                    "us_aqi": int(current.get("us_aqi", 0)) if current.get("us_aqi") is not None else None,
                },
                "hourly": {
                    "times": hourly.get("time", []),
                    "pm2_5": [float(v) if v is not None else None for v in hourly.get("pm2_5", [])],
                    "us_aqi": [int(v) if v is not None else None for v in hourly.get("us_aqi", [])],
                },
            }

    return aqi_by_zone


def save_json(data: Any, path: Path) -> bool:
    """Safely save JSON, preserving existing file on error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"WARNING: Failed to save {path}: {e}")
        return False


def main():
    """Orchestrate data fetching and saving."""
    parser = argparse.ArgumentParser(
        description="Fetch Smoke ETA data: fires, wind, AQI (live or replay archive)"
    )
    parser.add_argument(
        "--replay",
        type=str,
        help="Replay mode: fetch 3-day archive ending on YYYY-MM-DD (e.g., 2025-11-01). "
             "Saves to data/replay/ instead of data/",
    )
    args = parser.parse_args()

    if args.replay:
        # Replay mode: save to data/replay/
        out_dir = PROJECT_ROOT / "data" / "replay"
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"Replay mode: saving to {out_dir}/")
    else:
        # Live mode: save to data/
        out_dir = PROJECT_ROOT / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        print("Live mode: saving to data/")

    print("Fetching FIRMS fires...")
    fires = fetch_fires(replay_date=args.replay)
    if fires and save_json(fires, out_dir / "fires.json"):
        print(f"✓ Saved {len(fires)} fires")
    elif not fires:
        print("✗ No fires fetched; existing file unchanged")

    print("\nFetching wind grid...")
    wind = fetch_wind(replay_date=args.replay)
    if wind.get("grid") and save_json(wind, out_dir / "wind.json"):
        print(f"✓ Saved wind grid ({len(wind['grid'])} points)")
    else:
        print("✗ Wind fetch failed; existing file unchanged")

    print("\nFetching AQI by zone...")
    aqi = fetch_aqi(replay_date=args.replay)
    if aqi and save_json(aqi, out_dir / "aqi.json"):
        print(f"✓ Saved AQI for {len(aqi)} zones")
    else:
        print("✗ AQI fetch failed; existing file unchanged")

    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    if fires:
        print(f"Fires: {len(fires)} total")
        sample = fires[0]
        print(f"  Sample: lat={sample['lat']}, lon={sample['lon']}, confidence={sample['confidence']}, frp={sample.get('frp')}")

    if wind.get("grid"):
        print(f"Wind grid: {len(wind['grid'])} points")
        if wind.get("times"):
            sample = wind["grid"][0]
            speeds_sample = sample.get("speed_kmh", [])[:3]
            dirs_sample = sample.get("direction_deg", [])[:3]
            print(f"  Sample point: lat={sample['lat']}, lon={sample['lon']}")
            print(f"    Speed (first 3h, km/h): {speeds_sample}")
            print(f"    Direction (first 3h, deg): {dirs_sample}")

    if aqi:
        zones_path = PROJECT_ROOT / "data" / "zones.json"
        with open(zones_path) as f:
            zones = json.load(f)
        first_zone_id = zones[0]["id"]
        if first_zone_id in aqi:
            zone_aqi = aqi[first_zone_id]
            print(f"AQI for {first_zone_id}:")
            print(f"  Current: pm2_5={zone_aqi['current'].get('pm2_5')}, us_aqi={zone_aqi['current'].get('us_aqi')}")


if __name__ == "__main__":
    main()
