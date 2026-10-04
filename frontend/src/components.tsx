import { useEffect, useRef } from 'react'
import { fmtScore, fmtTime, type PracticePose, type ScoreBreakdown } from './api'

const COMPONENTS: { key: 'pose' | 'timing' | 'dynamics' | 'flow'; label: string; weight: number; help: string }[] = [
  { key: 'pose', label: 'Pose', weight: 50, help: 'Joint angles and limb directions compared with the reference.' },
  { key: 'timing', label: 'Timing', weight: 25, help: 'How close signature moves landed to the beat.' },
  { key: 'dynamics', label: 'Dynamics', weight: 15, help: 'Movement speed relative to the reference.' },
  { key: 'flow', label: 'Flow', weight: 10, help: 'Direction of movement and transitions.' },
]

export function ScoreBars({ result }: { result: ScoreBreakdown }) {
  return (
    <div className="bars">
      {COMPONENTS.map((c) => (
        <div className="bar-row" key={c.key} title={c.help}>
          <span className="bar-label">
            {c.label} <small>{c.weight}%</small>
          </span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, result[c.key]))}%` }} />
          </span>
          <span className="bar-value">{fmtScore(result[c.key])}</span>
        </div>
      ))}
    </div>
  )
}

export function ScoreDetail({ result }: { result: ScoreBreakdown }) {
  return (
    <div className="stack">
      <div className="score-hero">
        <div className="score-big">{fmtScore(result.total)}</div>
        <div className="muted">choreography-match estimate / 100</div>
      </div>
      <ScoreBars result={result} />

      {result.feedback && result.feedback.length > 0 && (
        <section className="card">
          <h3>Feedback</h3>
          <ul className="notes">
            {result.feedback.map((n, i) => (
              <li key={i} className={`note note-${n.kind}`}>
                <span className="note-tag">{n.kind === 'strength' ? 'Strength' : 'Improve'}</span>
                <p>{n.text}</p>
                {n.tip && <p className="muted">Tip: {n.tip}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.timeline && result.timeline.length > 0 && (
        <section className="card">
          <h3>Pose match over time</h3>
          <div className="timeline" role="img" aria-label="Pose match per two-second window">
            {result.timeline.map((w) => (
              <div key={w.start_s} className="tl-col" title={`${fmtTime(w.start_s)}–${fmtTime(w.end_s)}: ${w.pose.toFixed(0)}`}>
                <div className="tl-bar" style={{ height: `${Math.max(2, w.pose)}%` }} />
                <span>{fmtTime(w.start_s)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {result.events && result.events.length > 0 && (
        <section className="card">
          <h3>Signature moves</h3>
          <table className="table">
            <thead>
              <tr>
                <th>Move</th>
                <th>At</th>
                <th>Offset</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody>
              {result.events.map((e) => (
                <tr key={`${e.name}-${e.time_s}`}>
                  <td>{e.name}</td>
                  <td>{fmtTime(e.time_s)}</td>
                  <td>
                    {e.offset_ms == null
                      ? 'missed'
                      : e.offset_ms === 0
                        ? 'on time'
                        : `${Math.abs(e.offset_ms).toFixed(0)} ms ${e.offset_ms > 0 ? 'late' : 'early'}`}
                  </td>
                  <td>{e.score.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {result.coverage && (
        <p className="muted small">
          Tracking coverage: full body visible {(result.coverage.full_body_ratio * 100).toFixed(0)}% · weakest window{' '}
          {(result.coverage.min_window_coverage * 100).toFixed(0)}% · {result.analysis_version}
        </p>
      )}
    </div>
  )
}

const EDGES: [number, number][] = [
  [5, 6], [5, 7], [7, 9], [6, 8], [8, 10], [5, 11], [6, 12], [11, 12],
  [11, 13], [13, 15], [12, 14], [14, 16], [0, 5], [0, 6],
]

/** Animated stick figure of the reference, driven by a clock in song seconds. Practice only. */
export function StickFigure({ pose, getTime, size = 320 }: { pose: PracticePose; getTime: () => number; size?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    let raf = 0
    const draw = () => {
      const c = canvas.current
      const ctx = c?.getContext('2d')
      if (c && ctx) {
        const styles = getComputedStyle(c)
        const t = getTime()
        const i = Math.max(0, Math.min(pose.frames.length - 1, Math.round(t * pose.fps)))
        const frame = pose.frames[i]
        const pad = 0.08 * size
        const s = size - 2 * pad
        const offX = pad + (s - s * pose.aspect) / 2
        ctx.clearRect(0, 0, size, size)
        ctx.lineCap = 'round'
        ctx.lineWidth = size / 40
        ctx.strokeStyle = styles.getPropertyValue('--accent').trim() || '#f5b'
        for (const [a, b] of EDGES) {
          if (frame[a][2] < 0.3 || frame[b][2] < 0.3) continue
          ctx.beginPath()
          ctx.moveTo(offX + frame[a][0] * s, pad + frame[a][1] * s)
          ctx.lineTo(offX + frame[b][0] * s, pad + frame[b][1] * s)
          ctx.stroke()
        }
        if (frame[0][2] >= 0.3) {
          ctx.fillStyle = ctx.strokeStyle
          ctx.beginPath()
          ctx.arc(offX + frame[0][0] * s, pad + frame[0][1] * s, size / 28, 0, Math.PI * 2)
          ctx.fill()
        }
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [pose, getTime, size])

  return <canvas ref={canvas} width={size} height={size} className="stick" aria-label="Reference dancer (practice)" />
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="spinner-wrap" role="status">
      <div className="spinner" />
      {label && <span>{label}</span>}
    </div>
  )
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? <div className="error">{error}</div> : null
}
