# Replay Mode Implementation — Complete ✅

## Summary

Successfully added **Replay Mode** to Smoke ETA. The demo now works regardless of today's fire count by fetching and replaying historical data from real fire and weather archives.

## What Was Built

### 1. Extended `backend/fetch_data.py` ✅
- **New flag**: `--replay YYYY-MM-DD` (3-day window ending on that date)
- **FIRMS archive**: Uses VIIRS_SNPP_SP (standard product, 3+ days old)
- **Wind archive**: Open-Meteo historical API (archive-api.open-meteo.com)
- **AQI archive**: Open-Meteo air-quality historical data
- **Output**: Saves to `data/replay/` when in replay mode
- **Schema**: Identical to live data (fires.json, wind.json, aqi.json)

### 2. Updated `backend/simulate.py` ✅
- **New flag**: `--out PATH` (custom output file, defaults to data-dir/sim.json)
- **Existing flag**: `--data-dir PATH` (already supported)
- **Usage**: Works with both live and replay data directories
- **Example**: `python backend/simulate.py --data-dir data/replay --out data/replay/sim.json`

### 3. Updated `backend/api.py` ✅
- **Modified `_load()`**: Accepts `mode` parameter (live|replay)
- **New endpoint**: `GET /api/sim?mode=live|replay`
- **Default**: live mode (backward compatible)
- **Error handling**: Clear 404 messages indicating mode and missing file

### 4. New Helper Script `backend/find_replay_date.py` ✅
- **Command**: `python backend/find_replay_date.py` — lists candidate dates
- **Command**: `python backend/find_replay_date.py --run 2025-11-05` — full pipeline
- **Pipeline**:
  1. Fetch replay data (`fetch_data.py --replay`)
  2. Run simulation (`simulate.py` on replay data)
  3. Print zone ETA table
- **Candidates**: Oct 25 - Nov 15 (stubble-burning season, high fire activity expected)

### 5. Documentation ✅
- **REPLAY_MODE.md**: Quick start, API usage, severity threshold tuning
- **Code**: Docstrings and comments throughout

## Test Status: ✅ All 18 Tests Pass

```
✅ Wind Direction Convention .... 3 tests
✅ Fire Aggregation ............. 3 tests
✅ Physics (Decay) .............. 1 test
✅ Zone ETA ..................... 3 tests
✅ Reproducibility .............. 2 tests
✅ Data Validation .............. 4 tests
✅ Transport Physics ............ 2 tests
                               ─────────
   TOTAL ....................... 18 tests
```

**Key**: All tests pass with replay mode integrated. No breaking changes to existing functionality.

## Usage Examples

### Live Mode (Default)
```bash
# Fetch latest fires/wind/AQI
python backend/fetch_data.py

# Run simulation
python backend/simulate.py

# Access
curl http://localhost:8000/api/sim?mode=live
```

### Replay Mode
```bash
# Fetch 3-day archive (e.g., Oct 30 - Nov 1, 2025)
python backend/fetch_data.py --replay 2025-11-01

# Simulate
python backend/simulate.py --data-dir data/replay --out data/replay/sim.json

# Access
curl "http://localhost:8000/api/sim?mode=replay"
```

### Quick Demo
```bash
# Full pipeline for a single date
python backend/find_replay_date.py --run 2025-11-05
```

## Severity Thresholds

Tunable in `backend/simulate.py`:

```python
SEVERITY_THRESHOLDS = (0.02, 0.10, 0.30)
```

Maps normalized influence score to:
- **Low**: < 0.02
- **Moderate**: 0.02–0.10
- **High**: 0.10–0.30
- **Very High**: ≥ 0.30

To spread zones across severity bands, adjust thresholds and rerun simulation.

## Key Design Decisions

1. **Separate directories**: `data/` for live, `data/replay/` for historical
   - Allows both modes to coexist
   - No conflicts between live and replay runs

2. **Same schemas**: Replay data matches live format exactly
   - Zero frontend changes needed
   - API layer handles mode selection

3. **API query parameter**: `mode=live|replay` rather than separate endpoints
   - Simple, backward-compatible (defaults to live)
   - Semantic: same endpoint, different data source

4. **Hourly emission**: Particles emit over 24h, transport over next 24h
   - Prevents h0 arrivals (all particles at source at t=0)
   - Realistic ETA computation

5. **Local fire filtering**: 30 km threshold
   - Flags nearby fires (local_fires_nearby)
   - ETA excludes local sources, focuses on transported smoke

## Files Modified/Created

| File | Change |
|------|--------|
| `backend/fetch_data.py` | Added --replay flag, archive APIs |
| `backend/simulate.py` | Added --out flag, path creation |
| `backend/api.py` | Added mode parameter to _load(), /api/sim?mode= |
| `backend/find_replay_date.py` | **NEW**: Helper script for replay pipeline |
| `REPLAY_MODE.md` | **NEW**: Documentation |
| `backend/test_simulate.py` | Unchanged (all 18 tests pass) |

## Next Steps (Optional)

1. **Commit replay data**: Store data/replay/*.json in git (once a good date is found)
2. **Frontend integration**: Use `?mode=query` to toggle between live/replay in UI
3. **Historical analysis**: Compare hindcasts with actual AQI observations
4. **Threshold tuning**: Run several replay dates, adjust SEVERITY_THRESHOLDS for better spread

---

**Status**: ✅ Replay mode ready for demo. All tests passing. No existing functionality broken.
