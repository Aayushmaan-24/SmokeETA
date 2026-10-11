#!/usr/bin/env python3
"""
Smoke ETA — Strands agent for smoke arrival Q&A.

Uses Ollama (localhost:11434, qwen2.5:7b) with tools that read the same
sim.json / aqi.json files the rest of the backend uses. A template fallback
covers model errors, timeouts, and unreachable Ollama so the demo never shows
an error message.

Run from the project root:
    python -c "from backend.agent import ask; print(ask('hello'))"
"""

from __future__ import annotations

import json
import os
import pathlib
import warnings
from typing import Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as CETimeoutError

from strands import Agent, tool
from strands.models.ollama import OllamaModel

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_REPLAY_DIR = _DATA_DIR / "replay"

# Model config: Ollama only, never Bedrock. Model id is configurable via env.
_OLLAMA_HOST = "http://localhost:11434"
_OLLAMA_MODEL_ID = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")


# ---------------------------------------------------------------------------
# Helpers: load replay vs live data
# ---------------------------------------------------------------------------

def _load_sim(mode: str = "replay") -> dict:
    """Load sim.json. Replay uses data/replay/sim.json if it exists, else data/sim.json."""
    replay_path = _REPLAY_DIR / "sim.json"
    live_path = _DATA_DIR / "sim.json"
    if mode == "replay" and replay_path.exists():
        path = replay_path
    else:
        path = live_path
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_aqi(mode: str = "replay") -> dict:
    """Load aqi.json. Replay uses data/replay/aqi.json if it exists, else data/aqi.json."""
    replay_path = _REPLAY_DIR / "aqi.json"
    live_path = _DATA_DIR / "aqi.json"
    if mode == "replay" and replay_path.exists():
        path = replay_path
    else:
        path = live_path
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _zone_result_by_id(sim: dict, zone_id: str) -> dict | None:
    for z in sim.get("zones", []):
        if z.get("id") == zone_id:
            return z
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def list_zones(mode: str = "replay") -> dict:
    """Return all zone ids and names from the matching sim.json.

    Args:
        mode: 'live' or 'replay'. Defaults to 'replay'.
    """
    sim = _load_sim(mode)
    zones = sim.get("zones", [])
    return {
        "zones": [{"id": z["id"], "name": z.get("name", z["id"])} for z in zones],
        "count": len(zones),
        "mode": mode,
    }


@tool
def get_zone_ranking(mode: str = "replay") -> dict:
    """Return all zones sorted by arrival hour, each with arrival, peak and severity.

    Use this tool when comparing zones, or when asked which zone is first or
    worst; it gives the full ordered list so the agent can answer directly
    from data instead of guessing.

    Args:
        mode: 'live' or 'replay'. Defaults to 'replay'.
    """
    sim = _load_sim(mode)
    zones = sim.get("zones", [])
    rows = []
    for z in zones:
        rows.append({
            "zone_id": z["id"],
            "zone_name": z.get("name", z["id"]),
            "arrival_hour": z.get("arrival_hour"),
            "peak_hour": z.get("peak_hour"),
            "severity": z.get("severity", "None"),
        })
    rows.sort(key=lambda r: (r["arrival_hour"] if isinstance(r["arrival_hour"], (int, float)) else 999))
    return {
        "mode": mode,
        "count": len(rows),
        "ranking": rows,
    }


@tool
def get_fire_summary(mode: str = "replay") -> dict:
    """Return number of distinct fire sources and rough location of the biggest clusters.

    Args:
        mode: 'live' or 'replay'. Defaults to 'replay'.
    """
    sim = _load_sim(mode)
    sources = sim.get("meta", {}).get("sources", [])
    n_fires = sum(s.get("n_fires", 0) for s in sources)
    n_sources = len(sources)

    # Biggest clusters by FRP
    ranked = sorted(sources, key=lambda s: s.get("frp_sum", 0.0), reverse=True)
    top = ranked[:3]
    clusters = []
    for s in top:
        clusters.append(
            {
                "lat": s.get("lat"),
                "lon": s.get("lon"),
                "frp_sum": s.get("frp_sum"),
                "n_fires": s.get("n_fires"),
            }
        )
    return {
        "total_fire_hotspots": n_fires,
        "source_clusters": n_sources,
        "top_clusters": clusters,
        "mode": mode,
    }


@tool
def get_smoke_eta(zone: str, mode: str = "replay") -> dict:
    """Smoke ETA for a single zone from the matching sim.json.

    Returns arrival_hour, peak_hour, severity, local_fires_nearby, and the
    zone's influence score. All values come from the simulation output only.

    Args:
        zone: Zone id (e.g. 'narela', 'mundka').
        mode: 'live' or 'replay'. Defaults to 'replay'.
    """
    sim = _load_sim(mode)
    z = _zone_result_by_id(sim, zone)
    if z is None:
        return {
            "zone": zone,
            "error": f"Zone '{zone}' not found in {mode} sim.json",
        }
    return {
        "zone": z["id"],
        "zone_name": z.get("name", z["id"]),
        "arrival_hour": z.get("arrival_hour"),
        "peak_hour": z.get("peak_hour"),
        "severity": z.get("severity", "None"),
        "local_fires_nearby": bool(z.get("local_fires_nearby", False)),
        "influence_score": z.get("influence_score", 0.0),
        "mode": mode,
    }


@tool
def get_current_aqi(zone: str, mode: str = "replay") -> dict:
    """Current PM2.5 and AQI for a zone from aqi.json.

    Returns 'unavailable' (None values) when the zone is not present or has
    no current reading.

    Args:
        zone: Zone id matching a key in aqi.json.
        mode: 'live' or 'replay'. Defaults to 'replay'.
    """
    aqi = _load_aqi(mode)
    entry = aqi.get(zone)
    if not entry or not isinstance(entry, dict):
        return {"zone": zone, "pm2_5": None, "us_aqi": None, "status": "unavailable"}

    cur = entry.get("current")
    if not cur or not isinstance(cur, dict):
        return {"zone": zone, "pm2_5": None, "us_aqi": None, "status": "unavailable"}

    pm2_5 = cur.get("pm2_5")
    us_aqi = cur.get("us_aqi")
    if pm2_5 is None and us_aqi is None:
        return {"zone": zone, "pm2_5": None, "us_aqi": None, "status": "unavailable"}

    return {
        "zone": zone,
        "pm2_5": pm2_5,
        "us_aqi": us_aqi,
        "status": "ok",
        "mode": mode,
    }


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """
You are Smoke ETA, a smoke arrival assistant for Delhi zones. You have 5 tools:
list_zones, get_fire_summary, get_smoke_eta, get_current_aqi, get_zone_ranking.

RULES (follow strictly):
- Use the tools to get real numbers BEFORE answering.
- ONLY state numbers that appear in tool results. NEVER invent arrival times,
  peak hours, AQI, PM2.5, or fire counts.
- If a tool returns None/error/unavailable for a field, say so. Do NOT guess.
- Severity (Low/Moderate/High/Very High/None) is a RELATIVE smoke exposure
  estimate from a simplified model, NOT a health forecast or AQI prediction.
- In replay mode, ALWAYS state the answer is based on a past event
  (2024-11-01 window), a replay estimate, not a live forecast.
- Keep replies under 90 words.
- Never diagnose. Suggest a doctor for symptoms.
- Reply in the user's language (en/Hindi).
- Adapt to persona: school (outdoor sports/recess/trips), asthma (breathing,
  windows, outdoor time), outdoor_worker (schedule, breaks, exposure),
  commuter (travel timing, routes).
- When asked to compare zones, say which zone is first or worst, or rank zones
  by arrival, call get_zone_ranking(mode) and use its sorted results. Do not
  guess ordering from memory.
""".strip()


# ---------------------------------------------------------------------------
# Persona-aware fallback templates
# ---------------------------------------------------------------------------

_FALLBACK_TEMPLATES: dict[str, tuple[str, str]] = {
    # persona -> (en_template, hi_template)
    # Placeholders: {zone}, {zone_name}, {severity}, {arrival}, {peak}, {local}, {note}
    "school": (
        "Based on the smoke estimate for {zone} ({zone_name}), severity is "
        "{severity}. Smoke may arrive around hour {arrival} and peak near hour "
        "{peak}. {local} There are active fires in the region. For a school "
        "setting, consider postponing outdoor sports if severity is High or "
        "Very High; otherwise plan outdoor time earlier in the window. "
        "{note}",
        " {zone} ({zone_name}) के धुएँ अनुमान के अनुसार, गंभीरता {severity} "
        "है। धुआँ लगभग घंटा {arrival} में आएगा और {peak} घंटे के "
        "आस-पास चोटी मात्रा प्रदेश भरी होगी। {local} क्षेत्र में आग लग रही "
        "है। विद्यालय के लिए, अगर गंभीरता High या Very High है तो "
        "स्थानीय खेलों को देर से करने का विचार करें; अन्यथा आरंभिक "
        "समय में बाहर का समय योजना करें। {note}",
    ),
    "asthma": (
        "For {zone} ({zone_name}), the smoke estimate shows severity {severity} "
        "with possible arrival around hour {arrival} and peak near hour {peak}. "
        "{local} If you have asthma, keep windows shut during the peak window and "
        "limit outdoor time when severity is High or Very High; consult your "
        "doctor for personal advice. {note}",
        " {zone} ({zone_name}) के धुएँ अनुमान के अनुसार, धुएँ का प्रभाव {severity} "
        "है और यह घंटा {arrival} के आस-पास आएगा, चोटी मात्रा {peak} घंटे "
        "के आस-पास होगी। {local} अगर आपके पास asthma है, तो चोटी मात्रा "
        "वाले कल में खिड़कियाँ बंद रखें और जब गंभीरता High या Very High "
        "हो तो बाहर का समय सीमित करें; व्यक्तिगत सलाह के लिए doctor से "
        "संपर्क करें। {note}",
    ),
    "outdoor_worker": (
        "For {zone} ({zone_name}), severity is {severity} with smoke possibly "
        "arriving around hour {arrival} and peaking near hour {peak}. {local} "
        "If you work outdoors, schedule the heaviest work earlier in the window "
        "and take breaks indoors during the peak; this is a relative estimate, "
        "not a health forecast. {note}",
        " {zone} ({zone_name}) के धुएँ अनुमान के अनुसार, धुएँ का प्रभाव {severity} "
        "है और यह घंटा {arrival} के आस-पास आएगा, चोटी मात्रा {peak} घंटे के "
        "आस-पास होगी। {local} यदि आप बाहर काम करते हैं, तो भारी काम को "
        "प्रारंभ के आरंभ में योजना करें और चोटी वाले कलीन में विश्राम के "
        "लिए अंदर बैठें; यह एक संकेत अनुमान है, स्वास्थ्य अनुमान नहीं। {note}",
    ),
    "commuter": (
        "For {zone} ({zone_name}), severity is {severity} with smoke possibly "
        "arriving around hour {arrival} and peaking near hour {peak}. {local} "
        "If you commute, try to travel outside the peak window when severity is "
        "High or Very High; keep windows up in transit. This is a relative "
        "estimate, not a health forecast. {note}",
        " {zone} ({zone_name}) के धुएँ अनुमान के अनुसार, धुएँ का प्रभाव {severity} "
        "है और यह घंटा {arrival} के आस-पास आएगा, चोटी मात्रा {peak} घंटे के "
        "आस-पास होगी। {local} यदि आप कॉम्यूट करते हैं, तो जब गंभीरता High "
        "या Very High हो तो चोटी वाले समय के बाहर यात्रा करें; यात्रा के दौरान "
        "खिड़कियाँ बंद रखें। यह एक संकेत अनुमान है, स्वास्थ्य अनुमान नहीं। "
        "{note}",
    ),
}

_HI_SEV = {"Low": "कम", "Moderate": "मध्यम", "High": "उच्च",
           "Very High": "बहुत उच्च", "None": "कोई नहीं"}
_HI_ADVICE = {
    "school": "अगर प्रभाव उच्च या बहुत उच्च है, तो बाहरी खेल टाल दें; वरना खेल जल्दी करवा लें।",
    "asthma": "अस्थमा हो तो चरम समय में खिड़कियाँ बंद रखें और बाहर कम निकलें; निजी सलाह के लिए डॉक्टर से मिलें।",
    "outdoor_worker": "बाहर का भारी काम पहले निपटा लें और चरम समय में घर के अंदर आराम करें।",
    "commuter": "चरम समय से बाहर यात्रा करें और वाहन की खिड़कियाँ बंद रखें।",
}

def _build_fallback_hi(zone_id, eta, mode, persona):
    name = eta.get("zone_name") or zone_id
    sev = _HI_SEV.get(eta.get("severity", "None"), "अज्ञात")
    a, p = eta.get("arrival_hour"), eta.get("peak_hour")
    a_txt = f"{a:.0f}" if isinstance(a, (int, float)) else "अनुमानित नहीं"
    p_txt = f"{p:.0f}" if isinstance(p, (int, float)) else "अनुमानित नहीं"
    local = " आस-पास स्थानीय आग भी सक्रिय है।" if eta.get("local_fires_nearby") else ""
    note = ("यह 2024-11-01 की पिछली घटना पर आधारित अनुमान है, लाइव पूर्वानुमान नहीं।"
            if mode == "replay" else
            "यह सापेक्ष अनुमान है, स्वास्थ्य पूर्वानुमान नहीं।")
    advice = _HI_ADVICE.get(persona, _HI_ADVICE["commuter"])
    return (f"{name}: धुआँ लगभग घंटा {a_txt} पर पहुँचेगा और घंटा {p_txt} के आस-पास "
            f"चरम पर होगा। सापेक्ष प्रभाव: {sev}।{local} {advice} {note}")


def _build_fallback(zone_id: str, eta: dict, mode: str, persona: str, lang: str) -> str:
    """Build a short persona-aware alert from smoke_eta numbers."""
    if lang == "hi":
        return _build_fallback_hi(zone_id, eta, mode, persona)
    severity = eta.get("severity", "Unknown")
    arrival = eta.get("arrival_hour")
    peak = eta.get("peak_hour")
    local = "Local fires nearby." if eta.get("local_fires_nearby") else "No local fires nearby."

    arrival_txt = f"{arrival:.1f}" if isinstance(arrival, (int, float)) else "not estimated"
    peak_txt = f"{peak:.1f}" if isinstance(peak, (int, float)) else "not estimated"

    zone_name = eta.get("zone_name") or zone_id
    note = (
        "This is based on a past event (2024-11-01 window) in replay mode."
        if mode == "replay"
        else "This is a relative smoke estimate, not a health forecast."
    )

    templates = _FALLBACK_TEMPLATES.get(persona, _FALLBACK_TEMPLATES["commuter"])
    en_tpl, hi_tpl = templates

    if lang == "hi":
        text = hi_tpl.format(
            zone=zone_id,
            zone_name=zone_name,
            severity=severity,
            arrival=arrival_txt,
            peak=peak_txt,
            local=local,
            note=note,
        )
    else:
        text = en_tpl.format(
            zone=zone_id,
            zone_name=zone_name,
            severity=severity,
            arrival=arrival_txt,
            peak=peak_txt,
            local=local,
            note=note,
        )

    # Hard trim to stay under 90 words.
    words = text.split()
    if len(words) > 90:
        text = " ".join(words[:87]) + "..."
    return text


def _pick_first_zone_without_explicit_zone(mode: str) -> dict:
    """Return smoke_eta for the first zone that has an arrival, for 'which zone first' queries.

    Deprecated for general questions: prefer get_zone_ranking() for multi-zone answers.
    Kept because existing single-zone fallback paths still use it.
    """
    sim = _load_sim(mode)
    zones = sim.get("zones", [])
    arrived = [z for z in zones if z.get("arrived")]
    arrived.sort(key=lambda z: (z.get("arrival_hour") or 999))
    chosen = arrived[0] if arrived else (zones[0] if zones else None)
    if chosen is None:
        return {"zone": "unknown", "error": "No zones available."}
    return get_smoke_eta(chosen["id"], mode=mode)


# ---------------------------------------------------------------------------
# Ranking-based fallback answers (general / all-zone questions)
# ---------------------------------------------------------------------------

def _first_n_with_arrivals(rank, n=3):
    """Return up to n ranking entries that have an estimated arrival, in order."""
    out = []
    for item in rank:
        a = item.get("arrival_hour")
        if isinstance(a, (int, float)):
            out.append(item)
            if len(out) >= n:
                break
    return out


def _rank_summary(rank: list[dict], persona: str, lang: str, mode: str) -> str:
    """Build a concise, honest multi-zone comparison from a ranking list.

    Never invents arrival times. When no zone has an arrival, says so plainly
    and still names the first zone by the simulation's ordering.
    """
    if not rank:
        return _no_zones_message(mode, lang, persona)

    arrived = _first_n_with_arrivals(rank, 3)
    if arrived:
        parts = []
        for item in arrived:
            a = item.get("arrival_hour")
            p = item.get("peak_hour")
            a_txt = _fmt_hour(a, lang)
            p_txt = _fmt_hour(p, lang)
            sev = _sev_label(item.get("severity"), lang)
            local = "local fires nearby" if item.get("local_fires_nearby") else "no local fires nearby"
            line = _ranking_line(item["zone_name"], a_txt, p_txt, sev, local, lang)
            parts.append(line)
        return _ranking_template(parts, mode, persona, lang)

    # No zone has an arrival.
    first = rank[0]
    return _no_arrival_summary(first, rank, mode, persona, lang)


def _no_zones_message(mode: str, lang: str, persona: str) -> str:
    if lang == "hi":
        return (
            "धुएँ के Skipping zones. Smoke ETA data has no zones right now, so I can't compare them. "
            "Check back after data is loaded."
        )
    return (
        "Smoke ETA currently has no zone data, so I can't compare zones. "
        "Try again after the data is loaded."
    )


def _fmt_hour(v, lang: str) -> str:
    if not isinstance(v, (int, float)):
        return _word(v, lang)
    if lang == "hi":
        return f"{v:.0f} घंटे"
    return f"hour {v:.1f}"


def _word(v, lang: str) -> str:
    if lang == "hi":
        return "अनुमानित नहीं"
    return "not estimated"


def _sev_label(sev, lang: str) -> str:
    if lang == "hi":
        return {"Low": "कम", "Moderate": "मध्यम", "High": "उच्च",
                "Very High": "बहुत उच्च", "None": "कोई नहीं"}.get(sev, "अज्ञात")
    return sev or "Unknown"


def _ranking_line(name, arrival_txt, peak_txt, sev, local, lang: str) -> str:
    if lang == "hi":
        return f"- {name}: {sev}, लगभग {arrival_txt} पहुँच, चरम {peak_txt}; {local}."
    return f"- {name}: {sev}, arriving ~{arrival_txt}, peak ~{peak_txt}; {local}."


def _ranking_template(parts: list[str], mode: str, persona: str, lang: str) -> str:
    body = "\n".join(parts)
    advice = _persona_advice(persona, lang, mode)
    note = _note(mode, lang)
    if lang == "hi":
        return (
            "धुएँ अनुमान के अनुसार, सबसे पहले प्रभावित जोन:\n"
            + body
            + "\n\n"
            + advice
            + "\n"
            + note
        )
    return (
        "Based on the smoke estimate, the first affected zones are:\n"
        + body
        + "\n\n"
        + advice
        + "\n"
        + note
    )


def _no_arrival_summary(first: dict, rank: list[dict], mode: str, persona: str, lang: str) -> str:
    name = first.get("zone_name") or first.get("zone_id")
    sev = _sev_label(first.get("severity"), lang)
    local = "local fires nearby" if first.get("local_fires_nearby") else "no local fires nearby"
    if lang == "hi":
        intro = (
            "इस समय किसी भी जोन में धुएँ के पहुँचने का अनुमान नहीं है। "
            + f"{name} ({sev})। {local}."
        )
    else:
        intro = (
            f"No zone currently has a smoke arrival estimate. Leading zone: {name} ({sev}). {local}."
        )
    advice = _persona_advice(persona, lang, mode)
    note = _note(mode, lang)
    return intro + "\n\n" + advice + "\n" + note


def _persona_advice(persona: str, lang: str, mode: str) -> str:
    if lang == "hi":
        return {
            "school": "अगर गंभीरता उच्च या बहुत उच्च है, तो बाहरी गतिविधियाँ टालें; वरना बाहर का समय आरंभ में योजना करें।",
            "asthma": "अस्थमा हो तो खिड़कियाँ बंद रखें और बाहर कम निकलें; निजी सलाह के लिए डॉक्टर से मिलें।",
            "outdoor_worker": "बाहर का भारी काम पहले निपटाएँ और चरम समय में अंदर आराम करें।",
            "commuter": "चरम समय से बाहर यात्रा करें और वाहन की खिड़कियाँ बंद रखें।",
        }.get(persona, _PERSONA_ADVICE_HI["commuter"])
    return _PERSONA_ADVICE_EN.get(persona, _PERSONA_ADVICE_EN["commuter"])


_PERSONA_ADVICE_HI = {
    "school": "अगर गंभीरता उच्च या बहुत उच्च है, तो बाहरी गतिविधियाँ टालें; वरना बाहर का समय आरंभ में योजना करें।",
    "asthma": "अस्थमा हो तो खिड़कियाँ बंद रखें और बाहर कम निकलें; निजी सलाह के लिए डॉक्टर से मिलें।",
    "outdoor_worker": "बाहर का भारी काम पहले निपटाएँ और चरम समय में अंदर आराम करें।",
    "commuter": "चरम समय से बाहर यात्रा करें और वाहन की खिड़कियाँ बंद रखें।",
}

_PERSONA_ADVICE_EN = {
    "school": "If severity is High or Very High, postpone outdoor activities; otherwise plan outdoor time earlier.",
    "asthma": "If you have asthma, keep windows shut and limit outdoor time; consult a doctor for personal advice.",
    "outdoor_worker": "Do heavy outdoor work earlier and rest indoors during the peak.",
    "commuter": "Travel outside the peak window and keep vehicle windows up.",
}


def _note(mode: str, lang: str) -> str:
    if lang == "hi":
        if mode == "replay":
            return "यह 2024-11-01 की पिछली घटना पर आधारित अनुमान है, लाइव पूर्वानुमान नहीं।"
        return "यह सापेक्ष अनुमान है, स्वास्थ्य पूर्वानुमान नहीं।"
    if mode == "replay":
        return "This is based on a past event (2024-11-01 window) in replay mode."
    return "This is a relative smoke estimate, not a health forecast."


def _build_fallback_ranking(rank: list[dict], persona: str, lang: str, mode: str) -> str:
    """Build a concise, honest multi-zone comparison from the ranking list.

    This is used for general (no-zone) questions when the agent path is
    unavailable. It never invents arrival times or orderings.
    """
    if not rank:
        return _no_zones_message(mode, lang, persona)
    return _rank_summary(rank, persona, lang, mode)


# ---------------------------------------------------------------------------
# Agent + ask()
# ---------------------------------------------------------------------------

def _build_agent(system_prompt: str | None = None) -> Agent:
    """Build a Strands agent with the smoke tools and Ollama model."""
    model = OllamaModel(
        _OLLAMA_HOST,
        model_id=_OLLAMA_MODEL_ID,
        options={
            "temperature": 0.2,
            "num_predict": 160,
            "top_p": 0.9,
            "top_k": 40,
        },
        temperature=0.2,
        max_tokens=160,
        keep_alive="10m",
    )
    return Agent(
        model=model,
        system_prompt=system_prompt or _SYSTEM_PROMPT,
        tools=[list_zones, get_fire_summary, get_smoke_eta, get_current_aqi, get_zone_ranking],
    )


def _run_agent_turn(agent: Agent, prompt: str) -> Any:
    """Run a single agent turn so it can be executed on a worker thread.

    Strands ``Agent.__call__`` mutates internal state, so it is not safe to
    call concurrently on one instance. We serialize each turn on its own
    thread and wait for the result with a thread-safe timeout.
    """
    return agent(prompt)

def _tools_from_messages(agent) -> list[str]:
    """Names of tools the agent actually called (Strands toolUse blocks)."""
    names: list[str] = []
    try:
        for m in agent.messages:
            for b in (m.get("content") or []):
                if isinstance(b, dict) and "toolUse" in b:
                    n = b["toolUse"].get("name")
                    if n and n not in names:
                        names.append(n)
    except Exception:
        pass
    return names

def _grounded(answer: str, eta: dict, zone) -> bool:
    """Accept the agent's answer only if it states the real arrival hour."""
    if not zone:
        return True  # ranking path is checked via tools_used instead
    a = eta.get("arrival_hour")
    if not isinstance(a, (int, float)):
        return True
    n = str(int(a))
    dev = n.translate(str.maketrans("0123456789", "०१२३४५६७८९"))
    return n in answer or dev in answer

def ask(question, zone=None, persona="commuter", lang="en",
        mode="replay", timeout_s=90):
    if mode not in ("live", "replay"):
        mode = "replay"
    if persona not in ("school", "asthma", "outdoor_worker", "commuter"):
        persona = "commuter"
    if lang not in ("en", "hi"):
        lang = "en"

    # --- General (no zone) path: rank ALL zones -------------------------------
    if not zone:
        # Fetch the ordered ranking once; reuse for both agent facts and fallback.
        try:
            rank = get_zone_ranking(mode=mode).get("ranking", [])
        except Exception:
            rank = []
        if not rank:
            return {
                "answer": "No zone data is available right now.",
                "tools_used": [],
                "source": "fallback",
            }
        ranking_facts = _ranking_facts(rank)

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            agent = _build_agent()
            prompt = ranking_facts + _build_user_prompt(question, zone, persona, lang, mode)
            future = executor.submit(_run_agent_turn, agent, prompt)
            response = future.result(timeout=timeout_s)
            answer = _extract_answer(response)
            tools = _tools_from_messages(agent)
            if answer and tools:
                return {"answer": answer, "tools_used": tools, "source": "agent"}
        except Exception:
            pass   # timeout, Ollama down, model error -> ranking fallback below
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        # Fallback from the ranking, not from a single arbitrary zone.
        return {
            "answer": _build_fallback_ranking(rank, persona, lang, mode),
            "tools_used": ["get_zone_ranking"],
            "source": "fallback",
        }

    # --- Single-zone path (unchanged) -----------------------------------------
    fb_zone = zone
    try:
        eta = get_smoke_eta(fb_zone, mode=mode)
    except Exception:
        eta = {}
    if not eta or "error" in eta:
        return {
            "answer": f"Sorry, I could not load smoke data for '{fb_zone}' in {mode} mode.",
            "tools_used": ["get_smoke_eta"],
            "source": "fallback",
        }

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        agent = _build_agent()
        facts = (f"FACTS (from the smoke simulation, use these exact numbers): "
                 f"zone={eta.get('zone_name')}, arrival_hour={eta.get('arrival_hour')}, "
                 f"peak_hour={eta.get('peak_hour')}, severity={eta.get('severity')}.\n")
        prompt = facts + _build_user_prompt(question, zone, persona, lang, mode)
        future = executor.submit(_run_agent_turn, agent, prompt)
        response = future.result(timeout=timeout_s)
        answer = _extract_answer(response)
        tools = _tools_from_messages(agent)
        ok = _grounded(answer or "", eta, zone)
        if answer and ok:
            return {"answer": answer, "tools_used": tools, "source": "agent"}
    except Exception:
        pass   # timeout, Ollama down, model error -> fallback below
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return {
        "answer": _build_fallback(fb_zone, eta, mode=mode, persona=persona, lang=lang),
        "tools_used": ["get_smoke_eta"],
        "source": "fallback",
    }


def _ranking_facts(rank: list[dict]) -> str:
    """Inject the actual ordered ranking as immutable facts for the agent.

    The agent may still call tools, but for comparison questions it already
    has the real numbers and should not invent ordering or arrival times.
    """
    lines = ["FACTS (ordered smoke-zone ranking; use these exact values):"]
    for item in rank:
        a = item.get("arrival_hour")
        p = item.get("peak_hour")
        sev = item.get("severity", "None")
        arrived = isinstance(a, (int, float))
        a_txt = f"{a:.1f}" if arrived else "none"
        p_txt = f"{p:.1f}" if (arrived and isinstance(p, (int, float))) else "none"
        local = "local fires nearby" if item.get("local_fires_nearby") else "no local fires nearby"
        lines.append(
            f"- {item.get('zone_name')} ({item.get('zone_id')}): "
            f"arrival={a_txt}, peak={p_txt}, severity={sev}, {local}"
        )
    lines.append("\nRules: NEVER invent arrival/peak/severity. If a field is 'none', say 'no estimate'. "
                 "To say which zone is first/worst, state the FIRST zone in this list that has an arrival. "
                 "Do not reorder these zones yourself.")
    return "\n".join(lines) + "\n"


def _build_user_prompt(question: str, zone: str, persona: str, lang: str, mode: str) -> str:
    """User prompt that gives the agent the specific question and context.

    The prompt ends with an explicit ANSWER: instruction so the model doesn't
    stall after the tool block and return an empty completion.
    """
    if zone:
        q = (
            f"Question: {question}\n\n"
            f"Zone: {zone}\n"
            f"Persona: {persona}\n"
            f"Language: {lang}\n"
            f"Mode: {mode}\n"
        )
    else:
        q = (
            f"Question: {question}\n\n"
            f"Persona: {persona}\n"
            f"Language: {lang}\n"
            f"Mode: {mode}\n"
        )
    if mode == "replay":
        q += "REMINDER: This is replay mode. Say the answer is based on a past event (2024-11-01 window).\n"
    q += "\nUse the tools to get the numbers, then reply with the final ANSWER in the requested Language above (hi = Hindi in Devanagari script). Keep it under 90 words.\nANSWER:"
    return q


def _extract_answer(response: Any) -> str | None:
    """Pull the text answer out of a Strands agent response.

    Strands returns an AgentResult whose ``str()`` is the final assistant
    message text. The ``.to_dict()`` contains the full message block.
    """
    try:
        if response is None:
            return None

        # 1. AgentResult.__str__ returns the final message text.
        s = str(response).strip()
        if s:
            return s

        # 2. Pull text out of to_dict message blocks.
        if hasattr(response, "to_dict"):
            d = response.to_dict()
            msg = d.get("message") if isinstance(d, dict) else None
            if isinstance(msg, dict):
                blocks = msg.get("content") or []
                texts = []
                for b in blocks:
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            t = b.get("text")
                            if isinstance(t, str) and t.strip():
                                texts.append(t)
                        elif b.get("type") in ("tool_result", "tool_use"):
                            # tool calls shouldn't be treated as the final answer
                            continue
                    elif isinstance(b, str):
                        texts.append(b)
                if texts:
                    joined = "".join(texts).strip()
                    if joined:
                        return joined

        # 3. Ollama sometimes returns raw response text at the top level.
        if isinstance(d, dict):
            for key in ("response", "output", "content", "text"):
                v = d.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass
    return None


def _extract_tools_used(response: Any) -> list[str]:
    """Return the names of tools the agent called during this turn."""
    try:
        if not hasattr(response, "to_dict"):
            return []
        d = response.to_dict()
        msg = d.get("message") if isinstance(d, dict) else None
        if not isinstance(msg, dict):
            return []
        tools = []
        blocks = msg.get("content") or []
        for b in blocks:
            if isinstance(b, dict):
                if b.get("type") == "tool_result":
                    name = b.get("name") or b.get("tool_name")
                    if isinstance(name, str) and name:
                        tools.append(name)
                elif b.get("type") == "tool_use":
                    name = b.get("name") or b.get("tool_name")
                    if isinstance(name, str) and name:
                        tools.append(name)
        return tools
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Quick self-check when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Agent module loaded. Ollama model:", _OLLAMA_MODEL_ID, "host:", _OLLAMA_HOST)
    print("Tools:", [f.__name__ for f in (list_zones, get_fire_summary, get_smoke_eta, get_current_aqi)])
    # Smoke-test the tools against replay data.
    print("\nlist_zones(replay):", list_zones(mode="replay"))
    print("\nget_fire_summary(replay):", get_fire_summary(mode="replay"))
    print("\nget_smoke_eta(narela, replay):", get_smoke_eta("narela", mode="replay"))
    print("\nget_current_aqi(narela, replay):", get_current_aqi("narela", mode="replay"))
