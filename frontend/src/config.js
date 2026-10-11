// Frontend-only configuration constants.

/**
 * Historical window covered by Replay mode. This is the single source of truth
 * for the Replay event date shown across the UI (zone detail badge, header,
 * map badge, Mode card, timeline badge).
 *
 * This is a display constant only: simulation timestamps used internally by the
 * model (e.g. meta.generated_at) are unchanged and must never be shown as the
 * historical event date.
 */
export const REPLAY_EVENT_WINDOW = '2024-10-30 to 2024-11-01'
