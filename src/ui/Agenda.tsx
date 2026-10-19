import { useEffect, useRef, useState } from 'react'
import { BRIDGE_HTTP_URL } from '../config'
import { useStore } from '../store'

/**
 * Today, under the J.A.R.V.I.S. name in the top-left: today's calendar events only, straight
 * from Apple Calendar (through the bridge's /agenda, which uses the JARVIS
 * Calendar helper). No other days, no reminders.
 *
 * Refreshes every few minutes and whenever the window regains focus; past
 * events dim and the next one is marked. Drag it by its title bar — with the
 * mouse or a pinching hand, which sends the same pointer events — and it stays
 * where it was put.
 */

type Agenda = {
  ok: boolean
  error?: string
  events: { title: string; start: string; end: string; allDay: boolean; location?: string }[]
}

const REFRESH_MS = 3 * 60_000
// v2: the box moved under the name; an offset saved from the old corner
// would push it somewhere odd, so start fresh.
const POS_KEY = 'jarvis.agenda.pos.v2'

const hhmm = (iso?: string) => (iso && iso.length >= 16 ? iso.slice(11, 16) : '')
const asDate = (iso: string) => new Date(iso.length === 16 ? `${iso}:00` : iso)

function loadPos(): { x: number; y: number } {
  try {
    const p = JSON.parse(localStorage.getItem(POS_KEY) ?? '')
    if (typeof p?.x === 'number' && typeof p?.y === 'number') return p
  } catch {
    /* first run */
  }
  return { x: 0, y: 0 }
}

export function AgendaBox() {
  const phase = useStore((s) => s.phase)
  const [data, setData] = useState<Agenda | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [pos, setPos] = useState(loadPos)
  const box = useRef<HTMLDivElement>(null)

  // Fetch now, every few minutes, and when the window comes back into focus.
  useEffect(() => {
    let alive = true
    const load = () =>
      fetch(`${BRIDGE_HTTP_URL}/agenda`)
        .then((r) => r.json())
        .then((d: Agenda) => alive && setData(d))
        .catch(() => alive && setData({ ok: false, error: 'bridge unreachable', events: [] }))
    void load()
    const t = setInterval(load, REFRESH_MS)
    window.addEventListener('focus', load)
    return () => {
      alive = false
      clearInterval(t)
      window.removeEventListener('focus', load)
    }
  }, [])

  // Re-evaluate "past" and "next" every minute.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])

  // Drag by the title bar. Listens on window, like the blades, so a hand's
  // synthetic pointer events keep driving it after leaving the header.
  const grab = (e: React.PointerEvent) => {
    e.preventDefault()
    const sx = e.clientX
    const sy = e.clientY
    const from = { ...pos }
    const move = (ev: PointerEvent) => {
      const r = box.current?.getBoundingClientRect()
      let x = from.x + (ev.clientX - sx)
      let y = from.y + (ev.clientY - sy)
      // Keep it on screen.
      if (r) {
        const baseLeft = r.left - pos.x
        const baseTop = r.top - pos.y
        x = Math.min(Math.max(x, -baseLeft), window.innerWidth - r.width - baseLeft)
        y = Math.min(Math.max(y, -baseTop), window.innerHeight - r.height - baseTop)
      }
      setPos({ x, y })
    }
    const done = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', done)
      window.removeEventListener('pointercancel', done)
      setPos((p) => {
        try {
          localStorage.setItem(POS_KEY, JSON.stringify(p))
        } catch {
          /* fine */
        }
        return p
      })
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', done)
    window.addEventListener('pointercancel', done)
  }

  if (phase === 'offline' || phase === 'boot') return null

  // Today only, whatever the bridge sent.
  const today = new Date(now).toDateString()
  const events = (data?.events ?? []).filter((e) => asDate(e.start).toDateString() === today || e.allDay)
  const nextIdx = events.findIndex((e) => !e.allDay && asDate(e.end).getTime() > now)
  const date = new Date(now).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })

  return (
    <div
      ref={box}
      className="agenda"
      style={{ transform: `translate(${pos.x}px, ${pos.y}px)` }}
    >
      <div className="agenda-head" onPointerDown={grab} title="Drag to move">
        <span>TODAY</span>
        <span className="agenda-date">{date}</span>
      </div>
      <div className="agenda-body">
        {!data && <div className="agenda-empty">Loading…</div>}
        {data && !data.ok && <div className="agenda-empty">Calendar unavailable</div>}
        {data?.ok && !events.length && (
          <div className="agenda-empty">Nothing on today.</div>
        )}
        {events.map((e, i) => {
          const past = !e.allDay && asDate(e.end).getTime() <= now
          return (
            <div key={`e${i}`} className={`agenda-row${past ? ' is-past' : ''}${i === nextIdx ? ' is-next' : ''}`}>
              <span className="agenda-time">{e.allDay ? 'ALL DAY' : hhmm(e.start)}</span>
              <span className="agenda-title">
                {e.title}
                {e.location ? <span className="agenda-sub">{e.location}</span> : null}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
