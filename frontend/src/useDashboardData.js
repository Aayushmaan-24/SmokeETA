import { useEffect, useState } from 'react'
import { getSim, getFires, getAqi, getZones } from './api.js'

/**
 * Loads all dashboard data in parallel; returns {data, error, loading, reload}.
 * data = null until everything required (sim + zones) is available.
 */
export function useDashboardData() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    Promise.all([getSim(), getFires(), getAqi(), getZones()])
      .then(([sim, fires, aqi, zones]) => {
        if (cancelled) return
        setData({ sim, fires, aqi, zones })
      })
      .catch((e) => {
        if (cancelled) return
        setError(e.message || String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [reloadKey])

  return { data, error, loading, reload: () => setReloadKey((k) => k + 1) }
}

/** Format hours (float) as "h 12" / "1d 4h" style label. */
export function formatHour(h) {
  if (h == null) return '—'
  if (h < 24) return `${Math.round(h)}h`
  return `${Math.floor(h / 24)}d ${Math.round(h % 24)}h`
}
