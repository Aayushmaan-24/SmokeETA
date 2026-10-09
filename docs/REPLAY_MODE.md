# Smoke ETA — Replay Mode

## Overview

Smoke ETA now supports **Replay Mode** to demonstrate the system regardless of current fire activity. Replay uses real historical fire, wind, and AQI data from Open-Meteo archives.

## Quick Start

### Live Mode (Default)
```bash
# Fetch latest data (last 2 days)
python backend/fetch_data.py

# Run simulation
python backend/simulate.py

# Access via API
curl http://localhost:8000/api/sim
```

### Replay Mode (Historical)
```bash
# Fetch 3-day archive ending on a specific date
python backend/fetch_data.py --replay 2025-11-05

# Run simulation on replay data
python backend/simulate.py --data-dir data/replay --out data/replay/sim.json

# Access via API
curl "http://localhost:8000/api/sim?mode=replay"
```

### Find Good Dates
```bash
# List candidate dates with expected high fire activity
python backend/find_replay_date.py

# Run full replay pipeline on a date
python backend/find_replay_date.py --run 2025-11-05
```

## Data Storage

```
data/
├── fires.json          # Live FIRMS data (last 2 days)
├── wind.json           # Live Open-Meteo forecast (2 days)
├── aqi.json            # Live air quality (2 days)
├── sim.json            # Live simulation output
└── replay/
    ├── fires.json      # Archive FIRMS data (3-day window)
    ├── wind.json       # Archive Open-Meteo historical (3 days)
    ├── aqi.json        # Archive air quality (3 days)
    └── sim.json        # Replay simulation output
```

Replay data is committed to the repository so the demo works offline without API keys.

## API

### GET /api/sim?mode=live|replay
Returns simulation results for the specified mode.

```bash
# Live simulation
curl http://localhost:8000/api/sim?mode=live

# Replay simulation
curl http://localhost:8000/api/sim?mode=replay

# Other endpoints (support both modes implicitly)
curl "http://localhost:8000/api/fires?mode=replay"
curl "http://localhost:8000/api/wind?mode=replay"
```

## Severity Thresholds

Severity is computed from **normalized influence score** (mean particle mass across all frames, normalized by the max zone influence).

### Default Thresholds (backend/simulate.py)

```python
SEVERITY_THRESHOLDS = (0.02, 0.10, 0.30)
```

- **Low**: score < 0.02
- **Moderate**: 0.02 ≤ score < 0.10
- **High**: 0.10 ≤ score < 0.30
- **Very High**: score ≥ 0.30

### Tuning for Better Spread

If all zones cluster in one severity band, adjust thresholds:

```python
# More granular (push Low threshold lower)
SEVERITY_THRESHOLDS = (0.01, 0.05, 0.15)

# More aggressive (push High threshold higher)
SEVERITY_THRESHOLDS = (0.05, 0.20, 0.50)
```

Rerun the simulation after tuning:

```bash
python backend/simulate.py
# or
python backend/simulate.py --data-dir data/replay
```

## Key Assumptions & Limitations

- **Replay uses real data**: FIRMS satellite detections, Open-Meteo wind/AQI archives. Not a synthetic demo.
- **48-hour window**: Simulations cover 48 hours from the replay start date.
- **Particle emission over 24 hours**: First 24 hours emit particles hourly; next 24 hours transport them.
- **Local vs. transported**: Sources within 30 km of a zone are flagged as "local_fires_nearby" but excluded from ETA calculation. Arrival/peak/severity reflect only transported smoke.
- **Not a forecast**: This is a hindcast demonstration using historical data. Real-time predictions would use current fires + live forecast wind.

## Testing

All tests pass with replay mode:

```bash
python -m pytest backend/test_simulate.py -v
```

18 tests cover wind physics, fire aggregation, zone detection, reproducibility, and data validation.

---

**Note**: Replay mode requires valid FIRMS API credentials in `.env` to fetch fire archive data. Wind and AQI are free via Open-Meteo historical archive API.
