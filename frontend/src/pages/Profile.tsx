import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmtScore, type UserRecord } from '../api'
import { ErrorBox, Spinner } from '../components'

export default function Profile() {
  const { username = '' } = useParams()
  const [rec, setRec] = useState<UserRecord | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setRec(null)
    api.record(username).then(setRec).catch((e) => setError(e.message))
  }, [username])

  if (error) return <ErrorBox error={error} />
  if (!rec) return <Spinner />

  const stats: [string, string][] = [
    ['Dance Rating', rec.rating.toFixed(0)],
    ['Battles', String(rec.battles)],
    ['Wins', String(rec.wins)],
    ['Losses', String(rec.losses)],
    ['Draws', String(rec.draws)],
    ['Win rate', rec.win_rate == null ? 'N/A' : `${(rec.win_rate * 100).toFixed(1)}%`],
    ['Best score', fmtScore(rec.best_score)],
    ['Average score', fmtScore(rec.average_score)],
  ]

  return (
    <div className="stack">
      <h1>{rec.username}</h1>
      <div className="grid-4">
        {stats.map(([label, value]) => (
          <div className="stat" key={label}>
            <span className="stat-value">{value}</span>
            <span className="stat-label">{label}</span>
          </div>
        ))}
      </div>
      {Object.keys(rec.average_components).length > 0 && (
        <p className="muted small">
          Average components: Pose {rec.average_components.pose} · Timing {rec.average_components.timing} · Dynamics{' '}
          {rec.average_components.dynamics} · Flow {rec.average_components.flow}
        </p>
      )}
      <section className="card">
        <h3>Recent battles</h3>
        {rec.history.length === 0 ? (
          <p className="muted">No finished duels yet.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Challenge</th>
                <th>Opponent</th>
                <th>Score</th>
                <th>Result</th>
                <th>Rating</th>
              </tr>
            </thead>
            <tbody>
              {rec.history.map((h) => (
                <tr key={h.duel_id}>
                  <td>
                    <Link to={`/duels/${h.duel_id}`}>{new Date(h.finalized_at).toLocaleDateString()}</Link>
                  </td>
                  <td>{h.challenge}</td>
                  <td>
                    <Link to={`/users/${h.opponent}`}>{h.opponent}</Link>
                  </td>
                  <td>
                    {h.my_score.toFixed(1)} – {h.opponent_score.toFixed(1)}
                  </td>
                  <td>
                    <span className={`pill pill-${h.result}`}>{h.result}</span>
                  </td>
                  <td className={h.rating_delta >= 0 ? 'pos' : 'neg'}>
                    {h.rating_delta >= 0 ? '+' : ''}
                    {h.rating_delta.toFixed(1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <p className="muted small">
        Battles = wins + losses + draws. The Dance Rating reflects results against opponents, not absolute dance skill.
      </p>
    </div>
  )
}
