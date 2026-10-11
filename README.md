# Smoke ETA

**See smoke before it reaches you.** Smoke ETA estimates *when* stubble-burning smoke will reach 11 Delhi-NCR zones and tells people what to do about it, in English or Hindi, using a fully local AI agent built on the **Strands Agents SDK** (AWS open source).

Built for Environmental Hacks (Bharat Builds Tour, Event 02), Air track. Team Pair.exe.

## The problem

Every October and November, crop-residue fires in Punjab and Haryana send smoke toward Delhi. AQI apps show what the air is *now*. They do not say "it arrives at hour 26, peaks at hour 28." Schools, parents of asthmatic children, outdoor workers and commuters find out after the smoke has arrived.

## What it does

- Shows NASA FIRMS fire hotspots and an animated, wind-driven smoke simulation on a map.
- Gives each zone an estimated **arrival hour, peak hour and relative exposure** (Low to Very High).
- A **Smoke Assistant** (Strands agent + local Ollama model) answers questions such as "Should tomorrow's outdoor sports go ahead?" for different personas (school, asthma, outdoor worker, commuter), in English or Hindi.
- **Live mode** uses current fires and the wind forecast. **Replay mode** uses real data from a past stubble-burning event (2024-10-30 to 2024-11-01), so the demo works even when today's fire count is low.

## Architecture

```
NASA FIRMS (fires) + Open-Meteo (wind, air quality)
        |
  backend/fetch_data.py  ->  data/ (live)  and  data/replay/ (historical)
        |
  backend/simulate.py    ->  particle dispersion, zone ETAs  ->  sim.json
        |
  backend/api.py (FastAPI): /api/sim, /api/fires, /api/ask ...
        |                      |
  React + Leaflet UI     backend/agent.py (Strands Agents + Ollama)
```

## Built with Strands Agents (AWS open source)

The assistant is a [Strands Agents SDK](https://github.com/strands-agents/sdk-python) agent running on a **local Ollama model**. No AWS account, no paid APIs.

Tools the agent can call: `list_zones`, `get_fire_summary`, `get_smoke_eta`, `get_current_aqi`, `get_zone_ranking`.

Safety measures:
- The system prompt forbids inventing numbers; answers must use tool results.
- **Grounding check:** if an answer for a zone does not state the real simulated arrival hour, it is rejected.
- **Template fallback:** on a timeout, model error or rejected answer, a pre-written persona-aware alert (English and Hindi, Devanagari) is built from the same simulation numbers, so users never see an error or an invented figure. The UI shows whether an answer came from the agent or the fallback.

## How the simulation works

1. Fire hotspots are clustered into sources weighted by fire radiative power.
2. Particles are emitted hourly from each source and advected every 10 minutes using an interpolated hourly wind field (8x8 grid, 10 m wind), with random-walk diffusion and exponential mass decay.
3. For each zone, only smoke from sources more than 30 km away counts as *transported* (local fires are flagged separately). **Arrival** is the first hour accumulated mass in the zone's 12 km catchment crosses a threshold; **peak** is the hour of maximum mass.
4. Severity is a relative bucket across zones.

Wind direction convention: meteorological wind direction is where the wind comes *from*, so smoke moves toward direction + 180 degrees. This is covered by unit tests.

## Run it locally

Requirements: Python 3.11, Node 18+, [Ollama](https://ollama.com), a free [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/api/) map key.

```bash
# 1. Python environment
python -m venv .venv && source .venv/bin/activate
pip install fastapi uvicorn numpy pandas httpx python-dotenv "strands-agents[ollama]"

# 2. Key (never commit this file)
echo "FIRMS_KEY=your_key" > .env

# 3. Local model
ollama pull qwen2.5:3b
ollama serve            # leave running in its own terminal

# 4. Backend
export OLLAMA_MODEL=qwen2.5:3b
uvicorn backend.api:app --port 8000

# 5. Frontend (new terminal)
cd frontend && npm install && npm run dev
```

Cached replay data is committed in `data/replay/`, so Replay mode works immediately. To refresh:

```bash
python backend/fetch_data.py                      # live data
python backend/simulate.py
python backend/fetch_data.py --replay 2024-11-01  # 3-day historical window
python backend/simulate.py --data-dir data/replay --out data/replay/sim.json
```

Tests: `python -m pytest backend/ -q`

Note: on CPU-only machines the agent can take 30 to 40 seconds per answer; the UI shows a "Thinking" state.

## Data sources (all free)

- NASA FIRMS (VIIRS) active fire detections
- Open-Meteo forecast, historical archive and air-quality APIs
- OpenStreetMap map tiles

## Limitations (please read)

- This is a **simplified transport model, not a validated atmospheric forecast**. Wind is 10 m only; there is no chemistry, boundary-layer or terrain modelling. Decay and thresholds are tunable assumptions.
- Severity is a **relative exposure ranking inside one run**, not an AQI value or health forecast.
- Replay results describe a past event (2024-10-30 to 2024-11-01) and are labelled as such. Live mode may show little smoke when few fires are active.
- The assistant gives general precautions only, not medical advice.

## Team

Pair.exe: Aayushmaan Chakraborty and Ambali Gajinkar.
