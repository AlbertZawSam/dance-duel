import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Duel, type UserRecord, type Weekly } from '../api'
import { useAuth } from '../auth'
import { ErrorBox, Spinner } from '../components'

export default function Home() {
  const { user } = useAuth()
  const [weekly, setWeekly] = useState<Weekly | null>(null)
  const [record, setRecord] = useState<UserRecord | null>(null)
  const [duels, setDuels] = useState<Duel[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.weekly(), api.record(user!.username), api.duels()])
      .then(([w, r, d]) => {
        setWeekly(w)
        setRecord(r)
        setDuels(d)
      })
      .catch((e) => setError(e.message))
  }, [user])

  if (error) return <ErrorBox error={error} />
  if (!weekly || !record) return <Spinner />

  const invites = duels.filter((d) => d.status === 'invited' && d.role === 'opponent')
  const yourTurn = duels.filter(
    (d) => d.status === 'awaiting_attempts' && d.my_progress !== 'done' && !['queued', 'processing'].includes(d.my_progress),
  )
  const fmtDay = (iso: string) => new Date(iso).toLocaleDateString(undefined, { timeZone: weekly.timezone, month: 'short', day: 'numeric' })

  return (
    <div className="stack">
      <section className="grid-3">
        <div className="stat">
          <span className="stat-value">{record.rating.toFixed(0)}</span>
          <span className="stat-label">Dance Rating (experimental)</span>
        </div>
        <div className="stat">
          <span className="stat-value">
            {record.wins}–{record.losses}
            {record.draws ? `–${record.draws}` : ''}
          </span>
          <span className="stat-label">W–L{record.draws ? '–D' : ''}</span>
        </div>
        <div className="stat">
          <span className="stat-value">{record.win_rate == null ? 'N/A' : `${(record.win_rate * 100).toFixed(0)}%`}</span>
          <span className="stat-label">Win rate</span>
        </div>
      </section>

      {(invites.length > 0 || yourTurn.length > 0) && (
        <section className="card">
          <h3>Waiting on you</h3>
          <ul className="list">
            {invites.map((d) => (
              <li key={d.id}>
                <Link to={`/duels/${d.id}`}>
                  <b>{d.challenger.username}</b> challenged you to <b>{d.challenge.title}</b>
                </Link>
              </li>
            ))}
            {yourTurn.map((d) => (
              <li key={d.id}>
                <Link to={`/duels/${d.id}`}>
                  Your turn: <b>{d.challenge.title}</b> vs {d.role === 'challenger' ? d.opponent.username : d.challenger.username}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid-2">
        <section className="card">
          <h3>Trending this week</h3>
          {weekly.trending_challenges.length === 0 ? (
            <p className="muted">No finished duels yet this week.</p>
          ) : (
            <ol className="podium">
              {weekly.trending_challenges.map((c) => (
                <li key={c.challenge_id}>
                  <Link to={`/challenges/${c.slug}`}>{c.title}</Link>
                  <span className="muted small">
                    {c.duels} duels · {c.players} players
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
        <section className="card">
          <h3>Best dancers this week</h3>
          {weekly.best_users.length === 0 ? (
            <p className="muted">Nobody has {weekly.min_duels_for_best_users} finished duels yet this week.</p>
          ) : (
            <ol className="podium">
              {weekly.best_users.map((u) => (
                <li key={u.user_id}>
                  <Link to={`/users/${u.username}`}>{u.username}</Link>
                  <span className="muted small">
                    {u.rating.toFixed(0)} rating · {u.weekly_duels} duels
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
      <p className="muted small">
        Week of {fmtDay(weekly.week_start)} – {fmtDay(new Date(new Date(weekly.week_end).getTime() - 1).toISOString())} ({weekly.timezone}).
        Best dancers need at least {weekly.min_duels_for_best_users} finished duels this week; at most two against the same
        opponent count.
      </p>
    </div>
  )
}
