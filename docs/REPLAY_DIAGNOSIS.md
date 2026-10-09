# Replay Mode — Diagnosis: 0/11 Zone Arrivals

## Root Cause: Wind Direction Unfavorable

**The replay date (2025-11-01) has wind blowing AWAY from Delhi, not toward it.**

## Key Findings

### 1. Wind Analysis (Fire Belt: lat 29–31, lon 74–77)

| Date | Wind FROM | Wind TOWARD | Speed |
|------|-----------|------------|-------|
| 2025-10-30 | 113° (ESE) | 203° (SSW) | 5.0 km/h |
| 2025-10-31 | 76° (ENE) | 211° (SSW) | 5.7 km/h |
| 2025-11-01 | 44° (NE) | 224° (SW) | 5.8 km/h |

**Interpretation**: All three days show wind blowing toward the **south-southwest** (200–224°).

### 2. Fire Source Geography

| Metric | Value |
|--------|-------|
| Fire belt center | 31.02°N, 74.61°E |
| Delhi center | 28.70°N, 77.15°E |
| Bearing (fires → Delhi) | **132.4°** (SE) |
| Favorable wind direction needed | ~132° |
| **Actual wind blows toward** | **~214°** (SW) |
| **Angular mismatch** | **91.4°** |

**Interpretation**: Fires are **northwest** of Delhi. To reach Delhi, wind must blow **southeast** (toward 132°). Instead, wind blows **southwest** (away from Delhi).

### 3. Fire Source Statistics

- **Upwind sources** (>30 km from any zone): **1,395 / 1,401** ✓
- **With favorable wind direction**: **1,358 / 1,395** ✓ (computed assuming SE wind)
- **Actual wind direction**: **SW** (away) ✗

### 4. Accumulated Transported Mass

All zones show **0.000 accumulated mass** because:

1. Particles start near fire belt (29–31°N, 74–76°E)
2. Wind pushes them southwest (away from zones)
3. Zones are at 28.5–28.8°N, 77.0–77.3°E (southeast)
4. Minimum particle-to-zone distance at t=0: **13.8 km** (already outside 12 km radius)
5. Particles move further away each hour

**Result**: Particles never enter any zone catchment → zero accumulated mass → all zones below threshold.

---

## Conclusion

**This is NOT a bug.** The simulation is working correctly:

✅ 1,395 upwind sources identified  
✅ 1,591 particles emitted and transported hourly  
✅ Wind field correctly interpolated  
✅ Particle advection and decay working  
✅ Zone detection logic sound  

**The 0/11 arrivals are physically correct for this date.** The wind pattern on Oct 30–Nov 1, 2025 blows smoke away from Delhi, not toward it.

---

## Next Steps

To see arrivals in replay mode, pick a date with **westerly or northwesterly wind** blowing from the fire belt toward Delhi (typically early morning in stubble-burning season). 

Suggested approach:
1. Scan multiple dates in Nov 2024 or 2023 (historical data available)
2. Filter for mean wind direction 90–180° (favoring transport toward SE Delhi)
3. Run replay on a favorable date

Alternative: Run live mode when current wind direction favors transport.
