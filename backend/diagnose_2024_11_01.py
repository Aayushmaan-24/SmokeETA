#!/usr/bin/env python3
"""
Diagnose why 2024-11-01 replay still shows 0/11 zone arrivals despite favorable wind.
"""

import json
import math
import numpy as np
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent

def km_per_degree():
    lat_rad = np.radians(28.6)
    km_lat = 111.32
    km_lon = 111.32 * np.cos(lat_rad)
    return km_lat, km_lon

def circular_mean(angles_deg):
    if not angles_deg:
        return 0.0
    angles_rad = np.radians(angles_deg)
    sin_sum = np.sum(np.sin(angles_rad))
    cos_sum = np.sum(np.cos(angles_rad))
    mean_rad = np.arctan2(sin_sum, cos_sum)
    mean_deg = np.degrees(mean_rad)
    return mean_deg % 360

def main():
    print("="*90)
    print("DETAILED DIAGNOSIS: 2024-11-01 REPLAY, 0/11 ZONE ARRIVALS")
    print("="*90)

    sim = json.load(open(PROJECT_ROOT / "data/replay/sim.json"))
    wind = json.load(open(PROJECT_ROOT / "data/replay/wind.json"))
    zones = json.load(open(PROJECT_ROOT / "data/zones.json"))
    fires = json.load(open(PROJECT_ROOT / "data/replay/fires.json"))

    km_lat, km_lon = km_per_degree()

    # =========================================================================
    # 1. SIMULATION START TIME AND HOURLY WIND ANALYSIS
    # =========================================================================
    print("\n1. SIMULATION START TIME & HOURLY WIND OVER FIRE BELT")
    print("-" * 90)

    meta = sim["meta"]
    print(f"Generated at: {meta['generated_at']}")
    print(f"Duration: {meta['duration_hours']:.0f} hours")
    print(f"Timestep: {meta['timestep_minutes']} minutes\n")

    # Find the reference date from fire data
    if fires:
        sample_acq_date = fires[0].get("acq_date", "")
        print(f"Fire archive date (sample): {sample_acq_date}")
        # Extract start datetime from first fire
        start_date = sample_acq_date  # YYYYMMDD format in FIRMS
        if len(start_date) == 8:
            start_dt = datetime.strptime(start_date, "%Y%m%d")
            print(f"Simulation start (inferred): {start_dt.isoformat()}Z\n")

    # Analyze hourly wind over fire belt
    times = wind["times"]
    grid = wind["grid"]
    fire_belt_points = [g for g in grid if 29 <= g["lat"] <= 32 and 74 <= g["lon"] <= 77.5]

    print(f"Fire belt grid points: {len(fire_belt_points)}")
    print(f"\n{'Hour':<6} {'Time':<20} {'Wind FROM (°)':<16} {'Wind TOWARD (°)':<16} {'Speed (km/h)':<12}")
    print("-" * 90)

    for hour in range(int(meta["duration_hours"])):
        if hour < len(times):
            time_str = times[hour]

            # Collect wind data for belt points at this hour
            from_dirs = []
            speeds = []

            for pt in fire_belt_points:
                if hour < len(pt.get("direction_deg", [])):
                    from_deg = pt["direction_deg"][hour]
                    speed = pt.get("speed_kmh", [None])[hour] if hour < len(pt.get("speed_kmh", [])) else None
                    if from_deg is not None and speed is not None:
                        from_dirs.append(from_deg)
                        speeds.append(speed)

            if from_dirs:
                mean_from = circular_mean(from_dirs)
                mean_toward = (mean_from + 180) % 360
                mean_speed = np.mean(speeds)

                if hour % 4 == 0 or hour < 3 or hour >= int(meta["duration_hours"]) - 3:
                    print(f"{hour:<6} {time_str:<20} {mean_from:>6.1f}°       {mean_toward:>6.1f}°       {mean_speed:>6.1f}")

    # =========================================================================
    # 2. ACCUMULATED TRANSPORTED MASS PER ZONE
    # =========================================================================
    print("\n\n2. ACCUMULATED TRANSPORTED MASS PER ZONE (48 HOURS)")
    print("-" * 90)

    ARRIVAL_MASS_THRESHOLD = 0.001

    # Reconstruct total emitted mass from sources
    sources = meta.get("sources", [])
    total_emitted = sum(s.get("frp_sum", 0) for s in sources)
    threshold_mass = ARRIVAL_MASS_THRESHOLD * total_emitted

    print(f"Total emitted mass (sum of FRP): {total_emitted:.2f}")
    print(f"Arrival threshold: {ARRIVAL_MASS_THRESHOLD * 100:.2f}% = {threshold_mass:.6e}\n")

    print(f"{'Zone':<20} {'Max Mass':<18} {'% of Total':<14} {'vs Threshold':<15} {'Ratio':<10}")
    print("-" * 90)

    frames = sim["frames"]
    zone_max_mass = {}

    for z in zones:
        z_lat, z_lon = z["lat"], z["lon"]
        radius_km = z.get("radius_km", 12.0)
        zone_id = z["id"]

        max_mass = 0

        for frame in frames:
            lats = np.array(frame.get("lat", []))
            lons = np.array(frame.get("lon", []))
            masses = np.array(frame.get("mass", []))

            if lats.size == 0:
                continue

            d_km = np.sqrt(
                ((lats - z_lat) * km_lat) ** 2 + ((lons - z_lon) * km_lon) ** 2
            )
            in_zone = d_km <= radius_km
            frame_mass = float(np.sum(masses[in_zone]))
            max_mass = max(max_mass, frame_mass)

        zone_max_mass[zone_id] = max_mass
        pct_total = (max_mass / total_emitted * 100) if total_emitted > 0 else 0
        ratio = (max_mass / threshold_mass) if threshold_mass > 0 else 0

        status = "✓ ABOVE" if max_mass >= threshold_mass else "✗ BELOW"

        print(f"{z['name']:<20} {max_mass:.6e}   {pct_total:>6.3f}%      {status:<15} {ratio:>6.2f}x")

    # =========================================================================
    # 3. MINIMUM DISTANCE TO EACH ZONE, AND AT WHICH HOUR
    # =========================================================================
    print("\n\n3. MINIMUM DISTANCE ANY PARTICLE REACHES TO EACH ZONE")
    print("-" * 90)

    print(f"{'Zone':<20} {'Min Distance (km)':<20} {'At Hour':<10} {'Threshold (km)':<15}")
    print("-" * 90)

    for z in zones:
        z_lat, z_lon = z["lat"], z["lon"]
        radius_km = z.get("radius_km", 12.0)

        min_dist = float('inf')
        min_hour = None

        for frame in frames:
            hour = frame["hour"]
            lats = np.array(frame.get("lat", []))
            lons = np.array(frame.get("lon", []))

            if lats.size == 0:
                continue

            d_km = np.sqrt(
                ((lats - z_lat) * km_lat) ** 2 + ((lons - z_lon) * km_lon) ** 2
            )

            frame_min_dist = float(np.min(d_km))
            if frame_min_dist < min_dist:
                min_dist = frame_min_dist
                min_hour = hour

        status = "✓ INSIDE" if min_dist <= radius_km else "✗ OUTSIDE"
        print(f"{z['name']:<20} {min_dist:>7.1f}              {min_hour:>6.1f}h        {status:<15} ({radius_km:.1f} km)")

    # =========================================================================
    # 4. ROOT CAUSE ANALYSIS
    # =========================================================================
    print("\n\n4. ROOT CAUSE ANALYSIS")
    print("=" * 90)

    # Check threshold
    max_overall_mass = max(zone_max_mass.values())
    all_below_threshold = all(m < threshold_mass for m in zone_max_mass.values())

    # Check particles reaching zones
    min_distances = []
    for z in zones:
        z_lat, z_lon = z["lat"], z["lon"]
        radius_km = z.get("radius_km", 12.0)

        min_dist = float('inf')
        for frame in frames:
            lats = np.array(frame.get("lat", []))
            lons = np.array(frame.get("lon", []))
            if lats.size == 0:
                continue
            d_km = np.sqrt(
                ((lats - z_lat) * km_lat) ** 2 + ((lons - z_lon) * km_lon) ** 2
            )
            min_dist = min(min_dist, float(np.min(d_km)))

        min_distances.append(min_dist)

    avg_min_dist = np.mean(min_distances)
    any_inside = any(d <= 12 for d in min_distances)

    print(f"\nChecks:")
    print(f"  • Max accumulated mass over all zones: {max_overall_mass:.6e}")
    print(f"  • Threshold: {threshold_mass:.6e}")
    print(f"  • All zones below threshold: {all_below_threshold}")
    print(f"  • Any particle enters zone (within 12 km): {any_inside}")
    print(f"  • Average min distance to zones: {avg_min_dist:.1f} km")

    print(f"\n{'-'*90}")
    if not any_inside:
        print(f"❌ ROOT CAUSE: PARTICLES NOT REACHING ZONES")
        print(f"   Particles stay >12 km from all zone centers (avg {avg_min_dist:.1f} km)")
        print(f"   This could indicate:")
        print(f"     a) Simulation start time wrong (fires from wrong day)")
        print(f"     b) Particles emitted from wrong locations")
        print(f"     c) Wind field not matching actual transport")
    elif all_below_threshold:
        print(f"❌ ROOT CAUSE: THRESHOLD TOO STRICT")
        print(f"   Particles DO reach zones, but accumulated mass stays below threshold")
        print(f"   Max mass: {max_overall_mass:.6e} vs Threshold: {threshold_mass:.6e}")
        print(f"   Ratio: {max_overall_mass / threshold_mass:.2f}x")
    else:
        print(f"✓ ZONES SHOULD HAVE ARRIVALS (some particles reached with enough mass)")

if __name__ == "__main__":
    main()
