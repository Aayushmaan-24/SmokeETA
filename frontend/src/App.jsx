import { useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from 'react-leaflet'
import ParticleLayer, { SEVERITY_COLORS } from './ParticleLayer.jsx'
import { useDashboardData, formatHour } from './useDashboardData.js'
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
