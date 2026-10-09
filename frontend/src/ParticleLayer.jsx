import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { useMap } from 'react-leaflet'

// Severity colors (shared with zone markers in App.jsx / styles.css)
export const SEVERITY_COLORS = {
  None: '#9e9e9e',
  Low: '#2e7d32',
  Moderate: '#f9a825',
  High: '#ef6c00',
  'Very High': '#c62828',
}

/**
 * Canvas overlay that draws the current particle frame in place.
 *
 * The canvas is created once on mount inside a dedicated Leaflet pane; frame
 * updates only redraw pixels — the map is never recreated. A ref holds the
 * latest frame so redraws triggered by pan/zoom always use current data.
 */
export default function ParticleLayer({ frames, hour }) {
  const map = useMap()
  const canvasRef = useRef(null)
  const drawRef = useRef(null)
  const frameRef = useRef(null)

  // Mount: create pane + canvas, wire redraw on map movement.
  useEffect(() => {
    const pane = map.createPane('smokeParticles')
    pane.style.zIndex = 450
    pane.style.pointerEvents = 'none'
    const canvas = L.DomUtil.create('canvas', 'smoke-particle-canvas', pane)
    canvasRef.current = canvas

    drawRef.current = () => {
      const canvas = canvasRef.current
      if (!canvas) return
      const size = map.getSize()
      const topLeft = map.containerPointToLayerPoint([0, 0])
      L.DomUtil.setPosition(canvas, topLeft)
      if (canvas.width !== size.x || canvas.height !== size.y) {
        canvas.width = size.x
        canvas.height = size.y
      }
      const ctx = canvas.getContext('2d')
      ctx.clearRect(0, 0, size.x, size.y)

      const frame = frameRef.current
      if (!frame || !frame.lat?.length) return

      // Map geographic coords -> canvas pixels for the current view.
      const nw = map.containerPointToLatLng([0, 0])
      const se = map.containerPointToLatLng([size.x, size.y])
      const dLat = nw.lat - se.lat
      const dLon = se.lon - nw.lon
      if (dLat <= 0 || dLon <= 0) return
      const pxPerLon = size.x / dLon
      const pxPerLat = size.y / dLat

      for (let i = 0; i < frame.lat.length; i++) {
        const x = (frame.lon[i] - nw.lon) * pxPerLon
        const y = (nw.lat - frame.lat[i]) * pxPerLat
        if (x < -4 || y < -4 || x > size.x + 4 || y > size.y + 4) continue
        const mass = frame.mass[i]
        const alpha = Math.max(0.12, Math.min(0.85, mass * 4000))
        ctx.fillStyle = `rgba(90, 90, 90, ${alpha})`
        ctx.beginPath()
        ctx.arc(x, y, 2.2, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    const onMove = () => drawRef.current?.()
    map.on('move zoom viewreset resize', onMove)
    onMove()

    return () => {
      map.off('move zoom viewreset resize', onMove)
      canvasRef.current = null
      pane.remove()
    }
  }, [map])

  // Frame changes: pick nearest frame, redraw in place.
  useEffect(() => {
    if (!frames || frames.length === 0) {
      frameRef.current = null
    } else {
      frameRef.current = frames.reduce(
        (best, f) =>
          Math.abs(f.hour - hour) < Math.abs(best.hour - hour) ? f : best,
        frames[0],
      )
    }
    drawRef.current?.()
  }, [frames, hour, map])

  return null
}
