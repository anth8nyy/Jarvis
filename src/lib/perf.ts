/**
 * Light mode: the same interface, drawn more cheaply.
 *
 * The scene is the heaviest thing JARVIS does — on a laptop with memory to
 * spare it is fine, but alongside a dozen other apps it made the whole Mac
 * sluggish. Light mode draws at normal rather than Retina resolution, uses
 * fewer particles, keeps the glow but drops the subtle film effects, and
 * renders at 30 frames a second while he is only waiting. On by default; P
 * switches to full quality and back. Remembered between sessions.
 */

const KEY = 'jarvis.lite'

/** Always light now — the full-quality option was removed at the user's
 *  request. A stored choice from before is cleared. */
const lite = true
try {
  localStorage.removeItem(KEY)
} catch {
  /* nothing stored */
}

const listeners = new Set<() => void>()

export const isLite = (): boolean => lite

/** Kept for callers; light mode can no longer be turned off. */
export function setLite(next: boolean): void {
  void next
  for (const f of listeners) f()
}

export function onLite(f: () => void): () => void {
  listeners.add(f)
  return () => {
    listeners.delete(f)
  }
}
