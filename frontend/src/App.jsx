import { useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from 'react-leaflet'
import ParticleLayer, { SEVERITY_COLORS } from './ParticleLayer.jsx'
import { useDashboardData, formatHour } from './useDashboardData.js'
import { askAgent } from './api.js'
import L from 'leaflet'

const DEFAULT_ZOOM = 9

const SEVERITY_ORDER = ['Very High', 'High', 'Moderate', 'Low', 'None']

function severityColor(z) {
  return SEVERITY_COLORS[z.severity] || SEVERITY_COLORS.None
}

/** Fits the map view to zones + fire hotspots once, on first data load. */
function FitBounds({ points }) {
  const map = useMap()
  const did = useRef(false)
  useEffect(() => {
    if (did.current || points.length < 2) return
    did.current = true
    map.fitBounds(L.latLngBounds(points.map(([lat, lon]) => [lat, lon])), {
      padding: [30, 30],
    })
    window.__map = map // TEMP DEBUG
  }, [map, points])
  return null
}

export default function App() {
  const { data, error, loading, reload } = useDashboardData()
  const [hour, setHour] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const rafRef = useRef(null)
  const lastTsRef = useRef(null)

  const frames = data?.sim?.frames || []
  const zones = data?.zones || []
  const fires = data?.fires || []
  const aqi = data?.aqi || {}
  const durationH = data?.sim?.meta?.duration_hours ?? 48

  // --- playback loop ------------------------------------------------------
  useEffect(() => {
    if (!playing) return
    const tick = (ts) => {
      if (lastTsRef.current == null) lastTsRef.current = ts
      const dt = (ts - lastTsRef.current) / 1000
      lastTsRef.current = ts
      setHour((h) => {
        const next = h + dt * 2.5 // sim hours per real second
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
      (best, f) => (Math.abs(f.hour - hour) < Math.abs(best.hour - hour) ? f : best),
      frames[0],
    )
  }, [frames, hour])

  const selected = zones.find((z) => z.id === selectedId) || null
  const selectedAqi = selected ? aqi[selected.id] : null

  if (error) {
    return (
      <div className="container center">
        <h1>Smoke ETA</h1>
        <div className="card error-card">
          <h2>Data unavailable</h2>
          <p>{error}</p>
          <p className="muted">
            Make sure the FastAPI backend is running and the data files exist.
          </p>
          <button onClick={reload}>Retry</button>
        </div>
      </div>
  )
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

function AgentPanel({ zones, selectedId, onZoneChange }) {
  const [question, setQuestion] = useState('')
  // Sync with map/list selection; null = General question / All zones.
  const [zoneId, setZoneId] = useState(() => selectedId ?? '')
  const [persona, setPersona] = useState('school')
  const [lang, setLang] = useState('en')
  const [mode, setMode] = useState('replay')
  const [answer, setAnswer] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const askedRef = useRef(false)

  // Keep zoneId in sync when the map selection changes, but never clear
  // the user's other agent settings (persona/lang/mode/question).
  useEffect(() => {
    if (selectedId != null) {
      setZoneId(selectedId)
    }
  }, [selectedId])

  const zoneOptions = [
    { value: '', label: 'General question / All zones' },
    ...zones.map((z) => ({ value: z.id, label: `${z.name} (${z.id})` })),
  ]

  const selectedZone = zoneId
    ? zones.find((z) => z.id === zoneId) || null
    : null

  const handleAsk = async () => {
    if (loading) return
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
      <h2>Ask the smoke assistant</h2>
      <textarea
        className="agent-question"
        rows={2}
        placeholder="e.g. Which zone will be affected first?"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
      />

      <div className="agent-row">
        <label>Zone</label>
        <select value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
          {zoneOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="agent-row">
        <label>Persona</label>
        <select value={persona} onChange={(e) => setPersona(e.target.value)}>
          {PERSONAS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      <div className="agent-row">
        <label>Language</label>
        <select value={lang} onChange={(e) => setLang(e.target.value)}>
          {LANGS.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </select>
      </div>

      <div className="agent-row">
        <label>Mode</label>
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          {MODES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
      </div>

      <div className="agent-actions">
        <button
          className="agent-btn"
          disabled={!canAsk}
          onClick={handleAsk}
        >
          Ask
        </button>
        <span className="agent-status">
          {loading ? (
            <>
              <span className="agent-thinking">Thinking...</span>
              <span aria-label="live region" role="status"> </span>
            </>
          ) : answer ? (
            <span className="agent-source">
              <span className="dot" style={{ background: resSourceColor(answer.source) }} />
              {answer.source}
            </span>
          ) : (
            ''
          )}
        </span>
      </div>

      {error && <div className="agent-error">{error}</div>}

      {answer && (
        <div className="agent-meta">
          {answer.tools_used && answer.tools_used.length > 0 && (
            <span className="chip">Tools: {answer.tools_used.join(', ')}</span>
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

function resSourceColor(source) {
  if (source === 'agent') return SEVERITY_COLORS['High']
  if (source === 'fallback') return SEVERITY_COLORS['Low']
  return SEVERITY_COLORS.None
}
  if (loading || !data) {
    return (
      <div className="container center">
        <h1>Smoke ETA</h1>
        <p className="muted">Loading simulation data…</p>
      </div>
  )
}



  return (
    <div className="container">
      <header>
        <h1>Smoke ETA</h1>
        <p className="tagline">
          Estimating when smoke from agricultural fire hotspots may reach
          Delhi-NCR zones, using a 48-hour particle dispersion simulation
          driven by NASA FIRMS detections and hourly wind forecasts.
          <strong> Model-based estimates — not an AQI forecast.</strong>
        </p>
      </header>

      <div className="layout">
        <div className="map-col">
          <div className="map-wrap">
            <MapContainer
              center={[28.95, 77.6]}
              zoom={DEFAULT_ZOOM}
              scrollWheelZoom
              className="map"
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              <FitBounds
                points={[
                  ...zones.map((z) => [z.lat, z.lon]),
                  ...fires.slice(0, 200).map((f) => [f.lat, f.lon]),
                ]}
              />
              <ParticleLayer frames={frames} hour={hour} />
              {fires.map((f, i) => (
                <CircleMarker
                  key={`fire-${i}`}
                  center={[f.lat, f.lon]}
                  radius={4}
                  pathOptions={{
                    color: '#ff3d00',
                    fillColor: '#ff8a65',
                    fillOpacity: 0.9,
                    weight: 1,
                  }}
                >
                  <Tooltip>Fire hotspot — FRP {f.frp ?? 'n/a'}</Tooltip>
                </CircleMarker>
              ))}
              {zones.map((z) => (
                <CircleMarker
                  key={z.id}
                  center={[z.lat, z.lon]}
                  radius={selectedId === z.id ? 12 : 8}
                  eventHandlers={{ click: () => setSelectedId(z.id) }}
                  pathOptions={{
                    color: severityColor(z),
                    fillColor: severityColor(z),
                    fillOpacity: 0.85,
                    weight: selectedId === z.id ? 3 : 1.5,
                  }}
                >
                  <Tooltip>
                    {z.name} — {z.severity} (est.)
                  </Tooltip>
                </CircleMarker>
              ))}
            </MapContainer>

            <div className="legend card">
              <div className="legend-item">
                <span className="dot fire-dot" /> Fire hotspot (FIRMS)
              </div>
              <div className="legend-item">
                <span className="dot particle-dot" /> Simulated smoke particle
              </div>
              <div className="legend-item">
                <span className="dot zone-dot" /> Zone (colored by severity)
              </div>
              <div className="legend-sev">
                {SEVERITY_ORDER.map((s) => (
                  <span key={s} className="legend-item">
                    <span
                      className="dot"
                      style={{ background: SEVERITY_COLORS[s] }}
                    />{' '}
                    {s}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="timeline card">
            <div className="timeline-controls">
              <button onClick={() => setHour(0)} title="Reset">⏮</button>
              <button onClick={() => setPlaying((p) => !p)}>
                {playing ? '⏸ Pause' : '▶ Play'}
              </button>
              <span className="hour-display">
                Hour {hour.toFixed(1)} / {durationH}
              </span>
              <span className="muted frame-note">
                frame: {frameForHour ? `h${frameForHour.hour}` : '—'}
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
            />
            <div className="timeline-ticks">
              {[0, 12, 24, 36, 48].map((h) => (
                <span key={h}>{formatHour(h)}</span>
              ))}
            </div>
          </div>
        </div>

        <aside className="panel">
          <div className="card zone-list">
            <h2>Zones</h2>
            {zones.length === 0 && <p className="muted">No zones available.</p>}
            {zones.map((z) => (
              <button
                key={z.id}
                className={`zone-row ${selectedId === z.id ? 'active' : ''}`}
                onClick={() => setSelectedId(z.id)}
              >
                <span className="dot" style={{ background: severityColor(z) }} />
                <span className="zone-name">{z.name}</span>
                <span className="zone-eta">
                  {z.arrived ? `~${formatHour(z.arrival_hour)}` : 'No arrival'}
                </span>
              </button>
            ))}
          </div>

          {selected && (
            <div className="card zone-details">
              <h2>{selected.name}</h2>
              <div className="kv">
                <span>Status</span>
                <span>{selected.arrived ? 'Smoke arrival expected' : 'No arrival detected within 48 hours'}</span>
              </div>
              <div className="kv">
                <span>Arrival (est.)</span>
                <span>{selected.arrived ? formatHour(selected.arrival_hour) : '—'}</span>
              </div>
              <div className="kv">
                <span>Peak (est.)</span>
                <span>{selected.arrived ? formatHour(selected.peak_hour) : '—'}</span>
              </div>
              <div className="kv">
                <span>Severity (est.)</span>
                <span style={{ color: severityColor(selected) }}>{selected.severity}</span>
              </div>
              <div className="kv">
                <span>Relative influence</span>
                <span>{(selected.influence_score * 100).toFixed(1)}%</span>
              </div>
              {selectedAqi?.current && (
                <div className="kv">
                  <span>Current PM2.5 / US AQI</span>
                  <span>
                    {selectedAqi.current.pm2_5} µg/m³ · {selectedAqi.current.us_aqi}
                  </span>
                </div>
              )}
              <p className="muted small">{selected.status}</p>
            </div>
          )}
          {!selected && (
            <div className="card muted center-text">
              Select a zone on the map or list to see its smoke ETA details.
            </div>
          )}

          <div className="card disclaimer">
            <strong>Model limitations.</strong> This is a hackathon prototype, not a
            validated atmospheric model. Influence scores are relative comparisons
            between zones — they are not AQI values, and this tool does not predict
            future air quality.
          </div>

          <AgentPanel
            zones={zones}
            selectedId={selectedId}
            onZoneChange={setSelectedId}
          />

          <button
            className="regen"
            onClick={async () => {
              try {
                await fetch('/api/regenerate', { method: 'POST' }).then((r) => {
                  if (!r.ok) throw new Error(`HTTP ${r.status}`)
                })
                reload()
              } catch (e) {
                alert(`Regeneration failed: ${e.message}`)
              }
            }}
          >
            ↻ Regenerate simulation
          </button>
        </aside>
      </div>
    </div>
  )
}
