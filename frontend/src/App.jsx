import { useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from 'react-leaflet'
import ParticleLayer, { SEVERITY_COLORS } from './ParticleLayer.jsx'
import { useDashboardData, formatHour } from './useDashboardData.js'
import { askAgent, regenerate } from './api.js'
import L from 'leaflet'

const DEFAULT_ZOOM = 9
const SEVERITY_ORDER = ['Very High', 'High', 'Moderate', 'Low', 'None']

function severityColor(z) {
  return SEVERITY_COLORS[z.severity] || SEVERITY_COLORS.None
}

function severityBrightness(z) {
  // derived for accessible on-color when needed
  return SEVERITY_COLORS[z.severity] || SEVERITY_COLORS.None
}

/** Fits the map view to zones + fire hotspots once, on first data load. */
function FitBounds({ points, disabled }) {
  const map = useMap()
  const did = useRef(false)
  useEffect(() => {
    if (did.current || disabled || points.length < 2) return
    did.current = true
    map.fitBounds(
      L.latLngBounds(points.map(([lat, lon]) => [lat, lon])),
      { padding: [34, 34], maxZoom: 11 }
    )
  }, [map, points, disabled])
  return null
}

const PERSONAS = [
  { value: 'school', label: 'School' },
  { value: 'asthma', label: 'Asthma' },
  { value: 'outdoor_worker', label: 'Outdoor Worker' },
  { value: 'commuter', label: 'Commuter' },
]

const LANGS = [
  { value: 'en', label: 'English' },
  { value: 'hi', label: 'Hindi' },
]

const MODES = [
  { value: 'replay', label: 'Replay' },
  { value: 'live', label: 'Live' },
]

const RISK_LABEL = {
  'Very High': 'High smoke risk',
  High: 'Elevated smoke risk',
  Moderate: 'Moderate smoke risk',
  Low: 'Low smoke risk',
  None: 'No smoke risk modeled',
}

function niceError(e) {
  if (!e || !e.message) return 'The agent request failed.'
  const m = e.message || ''
  if (m.includes('Failed to fetch')) return 'Cannot reach the backend. Is it running on port 8000?'
  if (m.toLowerCase().includes('abort') || m.toLowerCase().includes('timeout'))
    return 'The agent took too long. Try again in a moment.'
  if (m.startsWith('HTTP ')) return `Backend returned ${m}.`
  return e.message
}

function resSourceDot(source) {
  if (source === 'agent') return '#C7F542'
  if (source === 'fallback') return '#b9b2a4'
  return '#8b8a7e'
}

// ---------- Agent panel (desktop-first, keyboard accessible) ----------

function AgentPanel({ zones, selectedId, onZoneChange }) {
  const [question, setQuestion] = useState('')
  const [zoneId, setZoneId] = useState(() => (selectedId != null ? String(selectedId) : ''))
  const [persona, setPersona] = useState('school')
  const [lang, setLang] = useState('en')
  const [mode, setMode] = useState('replay')
  const [answer, setAnswer] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const askedRef = useRef(false)

  // Keep zoneId in sync with map/list selection, but never clear other fields.
  useEffect(() => {
    if (selectedId != null) {
      setZoneId(String(selectedId))
    }
  }, [selectedId])

  const zoneOptions = [
    { value: '', label: 'General question / All zones' },
    ...zones.map((z) => ({ value: z.id, label: `${z.name} (${z.id})` })),
  ]

  const selectedZone = zoneId ? zones.find((z) => z.id === zoneId) || null : null

  const handleAsk = async () => {
    if (loading || question.trim().length === 0) return
    const body = { question, persona, lang, mode }
    if (zoneId) body.zone = zoneId

    setLoading(true)
    setError(null)
    askedRef.current = true
    try {
      const res = await askAgent(body)
      setAnswer(res)
    } catch (e) {
      setError(niceError(e))
      setAnswer(null)
    } finally {
      setLoading(false)
    }
  }

  const canAsk = question.trim().length > 0 && !loading

  return (
    <div className="card agent-panel">
      <div className="card-head">
        <span className="card-title">Smoke assistant</span>
        <span className="small muted">beta</span>
      </div>

      <textarea
        className="agent-question"
        rows={2}
        placeholder="Which Delhi-NCR zone will be affected first, and when?"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault()
            handleAsk()
          }
        }}
        aria-label="Question"
      />

      <div className="agent-grid" role="group" aria-label="Ask assistant options">
        <div className="agent-field">
          <label htmlFor="ag-zone">Zone</label>
          <select
            id="ag-zone"
            value={zoneId}
            onChange={(e) => setZoneId(e.target.value)}
            aria-describedby="zone-hint"
          >
            {zoneOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div className="agent-field">
          <label htmlFor="ag-persona">Persona</label>
          <select
            id="ag-persona"
            value={persona}
            onChange={(e) => setPersona(e.target.value)}
          >
            {PERSONAS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>

        <div className="agent-field">
          <label htmlFor="ag-lang">Language</label>
          <select
            id="ag-lang"
            value={lang}
            onChange={(e) => setLang(e.target.value)}
          >
            {LANGS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>

        <div className="agent-field">
          <label htmlFor="ag-mode">Mode</label>
          <select
            id="ag-mode"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            {MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <p className="small muted agent-note" id="zone-hint">
        Leave zone on “All zones” for a general question. Selecting a zone scopes
        the assistant to that Delhi-NCR area.
      </p>

      <div className="agent-actions">
        <button
          className="agent-btn"
          disabled={!canAsk}
          onClick={handleAsk}
          aria-label="Ask SmokeETA"
        >
          Ask
        </button>
        <span className="agent-status" aria-live="polite" aria-atomic="true">
          {loading ? (
            <>
              <span className="agent-thinking">Thinking</span>
            </>
          ) : answer ? (
            <span className="agent-source">
              <span
                className="dot"
                style={{ background: resSourceDot(answer.source), width: 7, height: 7 }}
              />
              {answer.source === 'agent' ? 'Agent' : 'Fallback'}
            </span>
          ) : (
            ''
          )}
        </span>
      </div>

      {error && <div className="agent-error" role="alert">{error}</div>}

      {answer && (
        <div className="agent-meta">
          {answer.tools_used && answer.tools_used.length > 0 && (
            <span className="chip">Used: {answer.tools_used.join(', ')}</span>
          )}
        </div>
      )}

      {answer && answer.answer != null && (
        <div className="agent-answer">{answer.answer}</div>
      )}

      {!answer && askedRef.current && !loading && !error && (
        <div className="agent-empty">Ask a question to see the answer here.</div>
      )}
    </div>
  )
}

// ---------- Selected zone details ----------

function ZoneDetails({ zone, aqi, isReplay, replayDate }) {
  if (!zone) {
    return (
      <div className="empty-state">
        Select a zone on the map or list to see its smoke ETA details.
      </div>
    )
  }

  const arrived = !!zone.arrived
  const aqiNow = aqi && aqi[zone.id]?.current

  return (
    <div className="zone-details">
      {isReplay && replayDate && (
        <div className="replay-tag">
          <span className="pulse" />
          Historical replay · {replayDate}
        </div>
      )}

      <div className="card-head" style={{ marginBottom: 6 }}>
        <span className="card-title">Selected zone</span>
        <span
          className={`zsev severity-${zone.severity} ${
            arrived ? 'arrived' : ''
          }`}
        >
          {zone.severity}
        </span>
      </div>

      <div className="lead-sm">{zone.name}</div>
      <div className="small muted" style={{ marginTop: -2, marginBottom: 8 }}>
        {RISK_LABEL[zone.severity] || 'No risk level'}
      </div>

      {arrived ? (
        <>
          <div>
            <div className="small muted uppercase" style={{ letterSpacing: '0.1em' }}>
              Smoke arrival (est.)
            </div>
            <div className="big">{formatHour(zone.arrival_hour)}</div>
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr auto',
              gap: '6px 14px',
              alignItems: 'baseline',
            }}
          >
            <span className="small muted uppercase" style={{ letterSpacing: '0.06em' }}>
              Peak (est.)
            </span>
            <span style={{ color: 'var(--text)', fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>
              {formatHour(zone.peak_hour)}
            </span>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr auto',
              gap: '6px 14px',
              alignItems: 'baseline',
            }}
          >
            <span className="small muted uppercase" style={{ letterSpacing: '0.06em' }}>
              Relative influence
            </span>
            <span style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
              {(zone.influence_score * 100).toFixed(1)}%
            </span>
          </div>
        </>
      ) : (
        <div className="noarr">
          No smoke arrival detected within the current {zone._dur ?? 48}h window.
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr auto',
          gap: '6px 14px',
          alignItems: 'baseline',
        }}
      >
        <span className="small muted uppercase" style={{ letterSpacing: '0.06em' }}>
          Fire hotspots within radius
        </span>
        <span style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
          {zone.nearby_fires ?? '—'}
        </span>
      </div>

      {aqiNow?.pm2_5 != null && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr auto',
            gap: '6px 14px',
            alignItems: 'baseline',
          }}
        >
          <span className="small muted uppercase" style={{ letterSpacing: '0.06em' }}>
            Current PM2.5 / US AQI
          </span>
          <span style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>
            {aqiNow.pm2_5} µg/m³ · {aqiNow.us_aqi}
          </span>
        </div>
      )}

      {zone.status != null && (
        <p className="small muted" style={{ marginTop: 8 }}>
          {zone.status}
        </p>
      )}
    </div>
  )
}

// ---------- Summary row ----------

function SummaryRow({ sim, fires, zones, isReplay, replayDate }) {
  const meta = sim?.meta ?? {}
  const totalParticles = meta.total_particles
  const nFrames = meta.n_frames
  const nZones = meta.n_zones
  const durationH = meta.duration_hours
  // Active fire sources: unique FIRMS hotspots with FRP > 0 in current data.
  const activeFires =
    fires && Array.isArray(fires)
      ? fires.filter((f) => f.frp != null && f.frp > 0).length
      : 0

  const mfDate = meta.generated_at ?? meta.replay_date ?? replayDate ?? null

  return (
    <div className="summaries" aria-label="Dashboard summary">
      <div className="summary">
        <div className="k">Monitored zones</div>
        <div className="v">{nZones ?? '—'}</div>
        <div className="sub">Delhi-NCR districts modeled</div>
      </div>

      <div className={activeFires > 0 ? 'summary warn' : ''}>
        <div className="k">Active fire sources</div>
        <div className="v">{activeFires}</div>
        <div className="sub">
          {activeFires > 0
            ? 'FIRMS hotspots in current data'
            : 'No FIRMS hotspots loaded'}
        </div>
      </div>

      <div className="summary">
        <div className="k">Forecast horizon</div>
        <div className="v">{formatHour(durationH)}</div>
        <div className="sub">
          {isReplay ? 'Replay window' : 'Simulation window'}
        </div>
      </div>

      <div className={isReplay ? 'summary warn' : 'summary'}>
        <div className="k">Mode</div>
        <div className="v">
          {isReplay ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: 'var(--orange)',
                  display: 'inline-block',
                }}
              />
              Replay
            </span>
          ) : (
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: 'var(--lime)',
                  display: 'inline-block',
                  boxShadow: '0 0 0 3px rgba(199,245,66,0.25)',
                }}
              />
              Live
            </span>
          )}
        </div>
        <div className="sub">
          {isReplay
            ? `Historical data · ${replayDate ?? mfDate ?? 'date unavailable'}`
            : mfDate ? `Generated · ${mfDate}` : 'Simulation pending'}
        </div>
      </div>
    </div>
  )
}

// ---------- Zone list with severity labels ----------

function ZoneList({ zones, selectedId, onSelect, isReplay }) {
  if (zones.length === 0) {
    return (
      <div className="empty-state">
        No zones available in the current data.
      </div>
    )
  }

  return (
    <div className="zone-list-wrap" role="list" aria-label="Monitored zones">
      {zones.map((z) => {
        const arrived = !!z.arrived
        return (
          <button
            key={z.id}
            className={`zone-row ${selectedId === z.id ? 'active' : ''}`}
            onClick={() => onSelect(z.id)}
            role="listitem"
            aria-pressed={selectedId === z.id}
          >
            <span className="zdot dot" style={{ background: severityColor(z) }} />
            <span className="zname">{z.name}</span>
            <span className="zmeta">
              {arrived ? formatHour(z.arrival_hour) : 'no arrival'}
            </span>
            <span className={`zsev severity-${z.severity} ${arrived ? 'arrived' : ''}`}>
              {z.severity}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ---------- Replay date helper ----------

function replayDateLabel(sim) {
  if (!sim) return null
  const m = sim.meta ?? {}
  // Prefer explicit replay_date, then generated_at, then null.
  if (m.replay_date) return m.replay_date
  if (m.generated_at) return m.generated_at.slice(0, 10)
  return null
}

// ---------- Error boundary-ish states ----------

function LoadingState() {
  return (
    <div className="main">
      <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
        <div
          style={{
            width: 22,
            height: 22,
            borderRadius: '50%',
            background: 'var(--lime)',
            margin: '0 auto 14px',
            boxShadow: '0 0 0 6px rgba(199,245,66,0.15)',
          }}
        />
        <div className="uppercase" style={{ color: 'var(--lime)', letterSpacing: '0.14em' }}>
          Loading intelligence
        </div>
        <div className="muted" style={{ fontSize: '0.85rem', marginTop: 4 }}>
          Pulling simulation, FIRMS hotspots, AQI, and zone data…
        </div>
      </div>
    </div>
  )
}

// ---------- Map legend ----------

const SEVERITY_LEGEND = SEVERITY_ORDER.map((s) => ({
  label: s,
  color: SEVERITY_COLORS[s],
}))

function MapLegend({ isReplay }) {
  return (
    <div className="legend" role="region" aria-label="Map legend">
      <div className="legend-title">Legend</div>
      <div className="legend-item">
        <span className="dot fire-dot" />
        <b>Fire hotspot</b>
        <span className="muted">(FIRMS)</span>
      </div>
      <div className="legend-item">
        <span className="dot particle-dot" />
        <b>Simulated smoke</b>
      </div>
      <div className="legend-item">
        <span className="dot zone-dot" />
        <b>Zone marker</b>
      </div>
      <div className="legend-sev" style={{ marginLeft: 'auto' }}>
        <span className="scale">Severity</span>
        {SEVERITY_LEGEND.map(({ label, color }) => (
          <span key={label} className="legend-item">
            <span className="dot" style={{ background: color }} />
            {label}
          </span>
        ))}
      </div>
      {isReplay && (
        <div className="legend-item">
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: 'var(--orange)',
              display: 'inline-block',
            }}
          />
          <b>Replay</b>
          <span className="muted">historical</span>
        </div>
      )}
    </div>
  )
}

// ---------- Main app ----------

export default function App() {
  const { data, error, loading, reload } = useDashboardData()
  const [hour, setHour] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const rafRef = useRef(null)
  const lastTsRef = useRef(null)

  const sim = data?.sim ?? null
  const frames = sim?.frames || []
  const zones = data?.zones || []
  const fires = data?.fires || []
  const aqi = data?.aqi || {}
  const meta = sim?.meta ?? {}
  const durationH = meta.duration_hours ?? 48
  const isReplay = meta.mode === 'replay' || (sim && sim._replay) || false
  const replayDate = replayDateLabel(sim)

  // Playback loop
  useEffect(() => {
    if (!playing) return
    const tick = (ts) => {
      if (lastTsRef.current == null) lastTsRef.current = ts
      const dt = (ts - lastTsRef.current) / 1000
      lastTsRef.current = ts
      setHour((h) => {
        const next = h + dt * 2.5
        return next >= durationH ? 0 : next
      })
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(rafRef.current)
      lastTsRef.current = null
    }
  }, [playing, durationH])

  const frameForHour = useMemo(() => {
    if (!frames.length) return null
    return frames.reduce(
      (best, f) =>
        Math.abs(f.hour - hour) < Math.abs(best.hour - hour) ? f : best,
      frames[0]
    )
  }, [frames, hour])

  const selected = zones.find((z) => z.id === selectedId) || null
  const selectedAqi = selected ? aqi[selected.id] : null

  const mapPoints = useMemo(
    () => [
      ...zones.map((z) => [z.lat, z.lon]),
      ...fires.slice(0, 200).map((f) => [f.lat, f.lon]),
    ],
    [zones, fires]
  )

  // Top-level fetch error
  if (error) {
    return (
      <div className="app">
        <div className="topbar">
          <div className="brand">
            <span className="brand-mark" />
            <span className="brand-name">
              Smoke<span>ETA</span>
            </span>
          </div>
        </div>
        <div className="main">
          <div className="card" style={{ maxWidth: 480, margin: '40px auto', textAlign: 'center' }}>
            <div className="uppercase" style={{ color: 'var(--orange)', letterSpacing: '0.12em', marginBottom: 8 }}>
              Data unavailable
            </div>
            <div style={{ color: 'var(--text)', fontSize: '0.95rem', marginBottom: 8 }}>
              {error}
            </div>
            <p className="muted" style={{ fontSize: '0.82rem', marginBottom: 16 }}>
              Make sure the FastAPI backend is running and the data files exist.
            </p>
            <button className="agent-btn" onClick={reload}>
              Retry load
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (loading || !data) {
    return (
      <div className="app">
        <div className="topbar">
          <div className="brand">
            <span className="brand-mark" />
            <span className="brand-name">
              Smoke<span>ETA</span>
            </span>
          </div>
        </div>
        <LoadingState />
      </div>
    )
  }

  const topbarMode = isReplay ? 'replay' : 'live'
  const topbarLabel = isReplay ? 'Replay' : 'Live'

  return (
    <div className="app">
      {/* ---------- Top bar ---------- */}
      <header className="topbar" role="banner">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">
            Smoke<span>ETA</span>
          </span>
        </div>

        <div className="topbar-context">
          <span>Delhi-NCR</span>
          <span className="sep" aria-hidden="true" />
          <span className="dim">Smoke dispersion intelligence</span>
        </div>

        <div
          className={`topbar-mode ${topbarMode}`}
          role="status"
          aria-live="polite"
          aria-label={`Dashboard mode: ${topbarLabel}${isReplay ? ', historical replay data' : ', live simulation mode'}`}
        >
          <span className="pulse" aria-hidden="true" />
          {topbarLabel}
        </div>
      </header>

      {/* ---------- Layout ---------- */}
      <div className="layout">
        {/* Sidebar */}
        <nav className="side" aria-label="Main navigation">
          <div className="side-section">
            <div className="nav">
              <button className="nav-item active" tabIndex={0}>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  aria-hidden="true"
                >
                  <path d="M12 21c-4 0-8-2-9-6" />
                  <path d="M6 14c2-3 4-5 6-5s4 2 6 5" fill="none" />
                  <circle cx="12" cy="9" r="1.6" fill="currentColor" stroke="none" />
                </svg>
                Dashboard
              </button>
              <button className="nav-item" tabIndex={-1}>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  aria-hidden="true"
                >
                  <path d="M12 21v-14M5 9l7-6 7 6" />
                  <path d="M5 9c2-4 5-7 7-7s5 3 7 7" fill="none" />
                </svg>
                Zones
              </button>
              <button className="nav-item" tabIndex={-1}>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  aria-hidden="true"
                >
                  <path d="M3 12h18M12 3a13 13 0 0 1 0 18 13 13 0 0 1 0-18z" />
                </svg>
                Timeline
              </button>
              <button className="nav-item" tabIndex={-1}>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  aria-hidden="true"
                >
                  <circle cx="12" cy="12" r="9" />
                  <path d="M9 12l2 2 4-4" />
                </svg>
                Assistant
              </button>
            </div>
          </div>

          <div className="side-cta">
            <button
              className="nav-item"
              onClick={async () => {
                try {
                  await regenerate()
                  reload()
                } catch (e) {
                  alert(`Regeneration failed: ${e.message}`)
                }
              }}
              style={{ color: 'var(--orange)', justifyContent: 'flex-start' }}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8" />
              </svg>
              Regenerate simulation
            </button>
            <p className="small muted" style={{ marginTop: 8, padding: '0 10px', lineHeight: 1.5 }}>
              Reruns the particle dispersion model using the existing simulator.
              Does not re-fetch live satellite data.
            </p>
          </div>
        </nav>

        {/* Main content */}
        <main className="main">
          {/* Masthead */}
          <div className="masthead">
            <div>
              <div className="eyebrow">Smoke dispersion intelligence · Delhi-NCR</div>
              <h1 className="lead">
                See smoke <em>before</em> it reaches you.
              </h1>
              <p className="muted" style={{ fontSize: '0.82rem', marginTop: 8, maxWidth: 680, lineHeight: 1.5 }}>
                Particle-dispersion estimates for {meta.n_zones ?? 'zones'} Delhi-NCR zones using
                NASA FIRMS hotspot detections and hourly wind forecasts.
                <span className="dim" style={{ display: 'block', marginTop: 4 }}>
                  Model-based estimates — not an AQI forecast or real-time advisory.
                </span>
              </p>
            </div>

            <div className="masthead-meta">
              <span>
                Run based on{' '}
                <strong className="tabular">
                  {meta.generated_at
                    ? meta.generated_at.slice(0, 16).replace('T', ' ')
                    : 'pending generation'}
                </strong>
              </span>
              {isReplay && replayDate && (
                <span>
                  Replay date ·{' '}
                  <strong className="tabular">{replayDate}</strong>
                </span>
              )}
            </div>
          </div>

          {/* Summary row: only real data from API responses */}
          <SummaryRow
            sim={sim}
            fires={fires}
            zones={zones}
            isReplay={isReplay}
            replayDate={replayDate}
          />

          {/* Map — focal point */}
          <section className="map-section" aria-label="Smoke dispersion map">
            <div className="map-wrap">
              <MapContainer
                center={[28.95, 77.6]}
                zoom={DEFAULT_ZOOM}
                scrollWheelZoom
                className="map"
                zoomControl={true}
              >
                <TileLayer
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                <FitBounds points={mapPoints} disabled={mapPoints.length < 2} />
                <ParticleLayer frames={frames} hour={hour} />

                {/* Fire hotspots */}
                {fires.map((f, i) => (
                  <CircleMarker
                    key={`fire-${i}`}
                    center={[f.lat, f.lon]}
                    radius={4}
                    pathOptions={{
                      color: '#2a0a00',
                      fillColor: '#FF783F',
                      fillOpacity: 0.95,
                      weight: 1,
                    }}
                  >
                    <Tooltip>
                      <strong>Fire hotspot</strong> · FRP {f.frp ?? 'n/a'}{' '}
                      <span className="muted">(NASA FIRMS)</span>
                    </Tooltip>
                  </CircleMarker>
                ))}

                {/* Zone markers — lime for neutral/primary data, severity color only for the marker ring */}
                {zones.map((z) => {
                  const isActive = selectedId === z.id
                  return (
                    <CircleMarker
                      key={z.id}
                      center={[z.lat, z.lon]}
                      radius={isActive ? 12 : 8}
                      eventHandlers={{ click: () => setSelectedId(z.id) }}
                      pathOptions={{
                        color: severityColor(z),
                        fillColor: isActive ? '#0c0e0b' : 'rgba(16,19,17,0.7)',
                        fillOpacity: isActive ? 1 : 0.55,
                        weight: isActive ? 3 : 1.5,
                      }}
                    >
                      <Tooltip>
                        <strong style={{ color: severityColor(z) }}>{z.name}</strong>
                        <span className="muted"> · {z.severity} (est.)</span>
                        {z.arrived && (
                          <div className="tabular" style={{ marginTop: 2 }}>
                            arrival ≈ {formatHour(z.arrival_hour)}
                          </div>
                        )}
                      </Tooltip>
                    </CircleMarker>
                  )
                })}
              </MapContainer>

              <div className="map-overlay-top">
                <span className={`map-eyebrow ${isReplay ? '' : 'live'}`}>
                  {isReplay ? 'Historical replay' : 'Live simulation'}
                </span>
                {frameForHour && (
                  <span className="map-eyebrow">
                    frame · h{frameForHour.hour.toFixed(1)}
                  </span>
                )}
              </div>

              <MapLegend isReplay={isReplay} />
            </div>
          </section>

          {/* Timeline */}
          <section className="timeline" aria-label="Smoke timeline">
            <div className="timeline-controls">
              <button
                onClick={() => {
                  setHour(0)
                  setPlaying(false)
                }}
                title="Reset to hour 0"
                aria-label="Reset timeline to start"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  width={14}
                  height={14}
                  aria-hidden="true"
                >
                  <path d="M5 5v14l9-7z" />
                </svg>
              </button>
              <button
                onClick={() => setPlaying((p) => !p)}
                aria-label={playing ? 'Pause playback' : 'Play playback'}
              >
                {playing ? (
                  <svg
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    width={14}
                    height={14}
                    aria-hidden="true"
                  >
                    <rect x="6" y="5" width="4" height="14" />
                    <rect x="14" y="5" width="4" height="14" />
                  </svg>
                ) : (
                  <svg
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    width={14}
                    height={14}
                    aria-hidden="true"
                  >
                    <path d="M6 5v14l12-7z" />
                  </svg>
                )}
              </button>
              <span className="hour-display tabular">
                h {hour.toFixed(1)} / {durationH.toFixed(0)}
              </span>
              <span className="muted frame-note">
                frame h {frameForHour ? frameForHour.hour.toFixed(1) : '—'}
              </span>
            </div>

            <input
              type="range"
              min={0}
              max={durationH}
              step={0.1}
              value={hour}
              onChange={(e) => {
                setPlaying(false)
                setHour(parseFloat(e.target.value))
              }}
              className="slider"
              aria-label="Timeline hour"
            />

            <div className="timeline-ticks">
              {[0, 12, 24, 36, 48].map((h) => (
                <span key={h}>{formatHour(h)}</span>
              ))}
            </div>
          </section>

          {/* Lower grid: zones + details */}
          <div className="lower">
            {/* Zones list */}
            <div className="card">
              <div className="card-head">
                <span className="card-title">Monitored zones</span>
                <span className="small muted">
                  {zones.length} zones · {isReplay ? 'replay' : 'live'}
                </span>
              </div>
              <ZoneList
                zones={zones}
                selectedId={selectedId}
                onSelect={setSelectedId}
                isReplay={isReplay}
              />
            </div>

            {/* Selected zone details */}
            <div className="card">
              <div className="card-head">
                <span className="card-title">Zone detail</span>
                {selected && (
                  <span className="small muted">
                    {selected.id}
                  </span>
                )}
              </div>
              <ZoneDetails
                zone={selected}
                aqi={aqi}
                isReplay={isReplay}
                replayDate={replayDate}
              />
            </div>
          </div>

          {/* AI assistant */}
          <section className="card agent-panel" style={{ marginTop: 14 }} aria-label="Smoke assistant">
            <AgentPanel
              zones={zones}
              selectedId={selectedId}
              onZoneChange={setSelectedId}
            />
          </section>

          {/* Disclaimer */}
          <p
            className="muted"
            style={{
              fontSize: '0.72rem',
              letterSpacing: '0.03em',
              marginTop: 14,
              maxWidth: 900,
              lineHeight: 1.5,
            }}
          >
            <strong>Model limitations.</strong> This is a hackathon prototype, not a validated
            atmospheric model. Influence scores are relative comparisons between zones — they are
            not AQI values, and this tool does not predict future air quality.
            Replay data is historical and labeled as such wherever the backend provides a date.
          </p>
        </main>
      </div>
    </div>
  )
}
