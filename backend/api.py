#!/usr/bin/env python3
"""
Smoke ETA — minimal FastAPI backend.

Serves the JSON files produced by fetch_data.py and simulate.py to the
frontend. Paths are resolved relative to the project root. No API keys or
.env values are ever exposed.

Run from the project root:
    uvicorn backend.api:app --reload --port 8000

Endpoints:
    GET /api/sim      -> data/sim.json (404 with hint if not generated yet)
    GET /api/fires    -> data/fires.json
    GET /api/wind     -> data/wind.json
    GET /api/aqi      -> data/aqi.json
    GET /api/zones    -> data/zones.json
    POST /api/regenerate -> reruns backend/simulate.run_simulation() and
                            saves data/sim.json (uses the existing simulator;
                            does not re-fetch live data)
    GET /api/health   -> liveness probe
"""

import json
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Allow importing the existing simulator module (backend/ has no __init__.py,
# so add it to sys.path directly).
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import simulate as sim_mod  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

app = FastAPI(title="Smoke ETA API", version="0.1.0")

# CORS for local frontend dev servers (Vite default ports).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load(name: str, mode: str = "live") -> object:
    """Load a data file relative to the project root with clear 404/500 errors."""
    if mode == "replay":
        path = DATA_DIR / "replay" / name
    else:
        path = DATA_DIR / name
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{name} not found in {mode} mode ({path}). "
            f"Run 'python backend/fetch_data.py' (live) or "
            f"'python backend/fetch_data.py --replay YYYY-MM-DD' (replay) first.",
        )
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"{name} is not valid JSON: {e}")


@app.get("/api/health")
def health() -> dict:
    """Liveness + which data files are currently available."""
    return {
        "status": "ok",
        "files": {n: (DATA_DIR / n).exists()
                  for n in ("fires.json", "wind.json", "aqi.json", "zones.json", "sim.json")},
    }


@app.get("/api/sim")
def get_sim(mode: str = "live") -> object:
    """Get simulation results. mode=live|replay."""
    if mode not in ("live", "replay"):
        raise HTTPException(status_code=400, detail="mode must be 'live' or 'replay'")
    return _load("sim.json", mode=mode)


@app.get("/api/fires")
def get_fires() -> object:
    return _load("fires.json")


@app.get("/api/wind")
def get_wind() -> object:
    return _load("wind.json")


@app.get("/api/aqi")
def get_aqi() -> object:
    return _load("aqi.json")


@app.get("/api/zones")
def get_zones() -> object:
    return _load("zones.json")


@app.post("/api/regenerate")
def regenerate() -> dict:
    """
    Regenerate the simulation using the existing simulator module.

    Runs synchronously and writes data/sim.json via the same code path as the
    CLI. Does NOT re-fetch live data. On failure the previous sim.json is
    untouched because run_simulation() only writes at the very end.
    """
    try:
        result = sim_mod.run_simulation()
    except sim_mod.InputError as e:
        raise HTTPException(status_code=409, detail=f"Cannot regenerate: {e}")
    except Exception as e:  # unexpected simulator failure
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")

    out = DATA_DIR / "sim.json"
    tmp = out.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sim_mod._clean(result), f, allow_nan=False)
        tmp.replace(out)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write sim.json: {e}")

    m = result["meta"]
    return {
        "status": "ok",
        "written": str(out),
        "total_particles": m["total_particles"],
        "n_frames": m["n_frames"],
        "n_zones": m["n_zones"],
        "generated_at": m["generated_at"],
        "elapsed_s": None,
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
