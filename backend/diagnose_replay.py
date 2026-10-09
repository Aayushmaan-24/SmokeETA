#!/usr/bin/env python3
"""
Diagnose why replay mode shows 0/11 zone arrivals.
1. Analyze wind direction and speed over the fire belt
2. Identify fire sources upwind of Delhi
3. Compute accumulated transported mass per zone vs. arrival threshold
"""

import json
import math
from pathlib import Path
from datetime import datetime
import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent

def load_json(path):
    with open(path) as f:
        return json.load(f)

def normalize_direction(deg):
    """Normalize direction to [0, 360)."""
    return deg % 360

def wind_from_to_toward(from_deg):
    """Convert meteorological 'from' direction to 'toward' direction."""
    toward = (from_deg + 180) % 360
    return toward

def direction_name(deg):
    """Convert degree to cardinal direction."""
    deg = normalize_direction(deg)
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int((deg + 11.25) / 22.5) % 16
    return dirs[idx]

def haversine(lat1, lon1, lat2, lon2):
    """Distance in km."""
    R = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def main():
    print("="*80)
    print("REPLAY MODE DIAGNOSIS")
    print("="*80)

    # Load data
    fires = load_json(PROJECT_ROOT / "data" / "replay" / "fires.json")
    wind = load_json(PROJECT_ROOT / "data" / "replay" / "wind.json")
    aqi = load_json(PROJECT_ROOT / "data" / "replay" / "aqi.json")
    zones = load_json(PROJECT_ROOT / "data" / "zones.json")
    sim = load_json(PROJECT_ROOT / "data" / "replay" / "sim.json")

    # Extract times and identify fire belt points (lat 29-31, lon 74-77)
    times = wind["times"]
    grid = wind["grid"]

    print(f"\n1. WIND ANALYSIS (Fire Belt: lat 29-31, lon 74-77)")
    print("-" * 80)

    # Identify fire belt grid points
    fire_belt_points = [g for g in grid if 29 <= g["lat"] <= 31 and 74 <= g["lon"] <= 77]
    print(f"Fire belt grid points: {len(fire_belt_points)}")

    # Group times by day
    days = {}
    for time_str in times:
        day = time_str[:10]  # YYYY-MM-DD
        if day not in days:
            days[day] = []
        days[day].append(time_str)

    print(f"Days in wind data: {sorted(days.keys())}\n")

    # Analyze wind per day
    for day in sorted(days.keys()):
        day_times = days[day]
        day_indices = [times.index(t) for t in day_times]

        # Collect directions and speeds for fire belt points at this day
        directions_toward = []
        speeds = []

        for pt in fire_belt_points:
            for idx in day_indices:
                from_deg = pt["direction_deg"][idx] if idx < len(pt["direction_deg"]) else None
                speed = pt["speed_kmh"][idx] if idx < len(pt["speed_kmh"]) else None
                if from_deg is not None and speed is not None:
                    toward_deg = wind_from_to_toward(from_deg)
                    directions_toward.append(toward_deg)
                    speeds.append(speed)

        if directions_toward:
            mean_toward = np.mean(directions_toward)
            mean_speed = np.mean(speeds)
            print(f"  {day}: Wind toward {direction_name(mean_toward)} ({mean_toward:.1f}°), mean speed {mean_speed:.1f} km/h")
        else:
            print(f"  {day}: No wind data")

    print(f"\n2. FIRE SOURCE ANALYSIS")
    print("-" * 80)

    # For each fire, compute distance to nearest zone
    fire_zones = []
    for fire in fires:
        min_dist = min(haversine(fire["lat"], fire["lon"], z["lat"], z["lon"]) for z in zones)
        fire_zones.append({
            "lat": fire["lat"],
            "lon": fire["lon"],
            "frp": fire.get("frp", 0),
            "min_dist_to_zone": min_dist
        })

    # Sources >30 km from all zones (upwind candidates)
    upwind_sources = [f for f in fire_zones if f["min_dist_to_zone"] > 30]
    print(f"Sources >30 km from any zone: {len(upwind_sources)}/{len(fires)}")

    if upwind_sources:
        # Check wind direction for upwind sources
        # Use mean wind toward direction from day 1
        first_day = sorted(days.keys())[0]
        day_times = days[first_day]
        day_indices = [times.index(t) for t in day_times]

        # Collect all "toward" directions over all fire belt points on day 1
        all_toward_dirs = []
        for pt in fire_belt_points:
            for idx in day_indices:
                from_deg = pt["direction_deg"][idx] if idx < len(pt["direction_deg"]) else None
                if from_deg is not None:
                    toward_deg = wind_from_to_toward(from_deg)
                    all_toward_dirs.append(toward_deg)

        mean_toward_global = np.mean(all_toward_dirs) if all_toward_dirs else 0

        # For each upwind source, check if it's upwind (wind pointing from source toward Delhi)
        # Delhi center: ~28.7°N, 77.2°E
        delhi_lat, delhi_lon = 28.7, 77.2

        pointing_toward_delhi = 0
        for src in upwind_sources:
            # Direction from source to Delhi
            bearing = math.degrees(math.atan2(
                delhi_lon - src["lon"],
                delhi_lat - src["lat"]
            ))
            bearing = (bearing + 360) % 360

            # Check if mean wind direction aligns with source->Delhi bearing (within ~90°)
            diff = abs(normalize_direction(mean_toward_global - bearing))
            if diff > 180:
                diff = 360 - diff

            if diff < 90:  # Wind blows from source toward Delhi
                pointing_toward_delhi += 1

        print(f"Upwind sources with favorable wind: {pointing_toward_delhi}/{len(upwind_sources)}")

    print(f"\n3. ACCUMULATED TRANSPORTED MASS PER ZONE")
    print("-" * 80)

    # Constants from simulate.py
    ARRIVAL_MASS_THRESHOLD = 0.001

    # Extract total emitted mass from simulation
    total_emitted = sim["meta"].get("total_emitted_mass", 0)
    print(f"Total emitted mass: {total_emitted:.6e}")
    print(f"Arrival threshold: {ARRIVAL_MASS_THRESHOLD * total_emitted:.6e} ({ARRIVAL_MASS_THRESHOLD*100:.2f}% of total)\n")

    print(f"{'Zone':<20} {'Max Trans. Mass':<18} {'% of Total':<12} {'vs Threshold':<15}")
    print("-" * 80)

    for zone_data in sim["zones"]:
        zone_name = zone_data["name"]
        max_mass = zone_data.get("max_transported_mass", 0)
        pct_total = (max_mass / total_emitted * 100) if total_emitted > 0 else 0
        threshold_pct = (ARRIVAL_MASS_THRESHOLD * 100)

        ratio = (max_mass / (ARRIVAL_MASS_THRESHOLD * total_emitted)) if total_emitted > 0 else 0

        status = "✓ ABOVE" if max_mass > ARRIVAL_MASS_THRESHOLD * total_emitted else "✗ BELOW"

        print(f"{zone_name:<20} {max_mass:.6e}   {pct_total:>6.3f}%      {status} ({ratio:.2f}x)")

    print(f"\n" + "="*80)
    print("DIAGNOSIS")
    print("="*80)

    # Summarize findings
    upwind_count = len(upwind_sources) if upwind_sources else 0
    all_zones_below = all(z.get("max_transported_mass", 0) < ARRIVAL_MASS_THRESHOLD * total_emitted
                           for z in sim["zones"])

    print(f"\nFindings:")
    print(f"  • Upwind sources (>30 km NW): {upwind_count}")
    if upwind_sources:
        print(f"  • Favorable wind direction: {pointing_toward_delhi}/{len(upwind_sources)}")
    print(f"  • All zones below threshold: {all_zones_below}")

    if upwind_count == 0:
        print(f"\n⚠️  ROOT CAUSE: No upwind sources outside 30 km radius.")
        print(f"    All fires are local (within 30 km). Local sources excluded from ETA.")
        print(f"    Result: 0 transported arrivals expected by design.")
    elif not (pointing_toward_delhi > 0 if upwind_sources else False):
        print(f"\n⚠️  ROOT CAUSE: Wind not favorable.")
        print(f"    Upwind sources exist, but wind direction not toward Delhi.")
    elif all_zones_below:
        print(f"\n⚠️  ROOT CAUSE: Transport threshold too high.")
        print(f"    Sources are upwind with favorable wind, but accumulated mass falls below threshold.")
        print(f"    Possible: Particles dispersing too much, too few particles per hour, or threshold set too strict.")

if __name__ == "__main__":
    main()
