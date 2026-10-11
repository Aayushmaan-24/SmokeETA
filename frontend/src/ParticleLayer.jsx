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
      // Use Leaflet's own projection so particles land in the right place.
      let maxMass = 0
      for (let i = 0; i < frame.mass.length; i++) {
        if (frame.mass[i] > maxMass) maxMass = frame.mass[i]
      }
      if (maxMass <= 0) maxMass = 1
  
      for (let i = 0; i < frame.lat.length; i++) {
        const p = map.latLngToContainerPoint([frame.lat[i], frame.lon[i]])
        if (p.x < -6 || p.y < -6 || p.x > size.x + 6 || p.y > size.y + 6) continue
        const norm = frame.mass[i] / maxMass
        const alpha = 0.35 + 0.5 * norm
        // soft halo, then solid core
        ctx.fillStyle = `rgba(70, 60, 85, ${alpha * 0.25})`
        ctx.beginPath()
        ctx.arc(p.x, p.y, 7, 0, Math.PI * 2)
        ctx.fill()
        ctx.fillStyle = `rgba(55, 45, 70, ${alpha})`
        ctx.beginPath()
        ctx.arc(p.x, p.y, 3, 0, Math.PI * 2)
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
