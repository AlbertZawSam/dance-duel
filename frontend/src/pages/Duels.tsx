import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmtScore, type Duel } from '../api'
import { ErrorBox, ScoreBars, Spinner } from '../components'

const STATUS_LABEL: Record<Duel['status'], string> = {
  invited: 'Invited',
  awaiting_attempts: 'Waiting for attempts',
  analyzing: 'Analyzing',
  finalized: 'Finished',
  declined: 'Declined',
  expired: 'Expired',
}

const PROGRESS_LABEL: Record<string, string> = {
  not_started: 'not started',
  started: 'dancing…',
  aborted: 'not started',
  queued: 'submitted, analyzing',
  processing: 'submitted, analyzing',
  invalid: 'needs a retry',
  technical_failure: 'needs a retry',
  done: 'finished',
}

const opponentOf = (d: Duel) => (d.role === 'challenger' ? d.opponent : d.challenger)

export function DuelList() {
  const [items, setItems] = useState<Duel[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.duels().then(setItems).catch((e) => setError(e.message))
  }, [])
  if (error) return <ErrorBox error={error} />
  if (!items) return <Spinner />
  return (
    <div className="stack">
      <h1>Duels</h1>
      {items.length === 0 && (
        <p className="muted">
          No duels yet. Pick a <Link to="/challenges">challenge</Link> and invite a friend.
        </p>
      )}
      <ul className="duel-list">
        {items.map((d) => {
          const me = d.role === 'challenger' ? d.outcome?.challenger : d.outcome?.opponent
          const result = d.outcome ? (d.outcome.is_draw ? 'Draw' : d.outcome.winner === opponentOf(d).username ? 'Loss' : 'Win') : null
          return (
            <li key={d.id}>
              <Link to={`/duels/${d.id}`} className="card duel-row">
                <div>
                  <b>{d.challenge.title}</b> vs {opponentOf(d).username}
                  <div className="muted small">{new Date(d.created_at).toLocaleString()}</div>
                </div>
                <div className="duel-status">
                  {result ? (
                    <span className={`pill pill-${result.toLowerCase()}`}>
                      {result} · {fmtScore(me?.total)}
                    </span>
                  ) : (
                    <span className="pill">{STATUS_LABEL[d.status]}</span>
                  )}
                </div>
              </Link>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function DuelDetail() {
  const { id = '' } = useParams()
  const [duel, setDuel] = useState<Duel | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => api.duel(id).then(setDuel).catch((e) => setError(e.message)), [id])
  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {
    if (duel?.status !== 'analyzing' && duel?.status !== 'awaiting_attempts') return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [duel?.status, load])

  const act = async (fn: (id: string) => Promise<Duel>) => {
    setError(null)
    try {
      setDuel(await fn(id))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!duel) return error ? <ErrorBox error={error} /> : <Spinner />
  const opp = opponentOf(duel)
  const canDance =
    duel.status === 'awaiting_attempts' &&
    !['done', 'queued', 'processing'].includes(duel.my_progress) &&
    duel.attempts_left > 0 &&
    new Date(duel.expires_at) > new Date()

  return (
    <div className="stack">
      <div>
        <h1>
          {duel.challenger.username} <span className="muted">vs</span> {duel.opponent.username}
        </h1>
        <p className="muted">
          <Link to={`/challenges/${duel.challenge.slug}`}>{duel.challenge.title}</Link> · locked to version{' '}
          {duel.challenge_version} · <span className="pill">{STATUS_LABEL[duel.status]}</span>
        </p>
      </div>
      <ErrorBox error={error} />

      {duel.status === 'invited' && duel.role === 'opponent' && (
        <div className="row">
          <button className="hot" onClick={() => act(api.acceptDuel)}>
            Accept duel
          </button>
          <button className="secondary" onClick={() => act(api.declineDuel)}>
            Decline
          </button>
        </div>
      )}
      {duel.status === 'invited' && duel.role === 'challenger' && <p>Waiting for {opp.username} to accept.</p>}

      {(duel.status === 'awaiting_attempts' || duel.status === 'analyzing') && (
        <section className="card stack">
          <div className="grid-2">
            <div>
              <div className="muted small">You</div>
              <b>{PROGRESS_LABEL[duel.my_progress] ?? duel.my_progress}</b>
            </div>
            <div>
              <div className="muted small">{opp.username}</div>
              <b>{PROGRESS_LABEL[duel.opponent_progress] ?? duel.opponent_progress}</b>
            </div>
          </div>
          <p className="muted small">
            Scores stay hidden until both of you finish. Closes {new Date(duel.expires_at).toLocaleString()} · {duel.attempts_left}{' '}
            official attempt{duel.attempts_left === 1 ? '' : 's'} left.
          </p>
          {canDance && (
            <Link className="button hot" to={`/play/${duel.challenge.slug}?mode=official&duel=${duel.id}`}>
              {duel.my_progress === 'invalid' || duel.my_progress === 'technical_failure' ? 'Retry official attempt' : 'Dance now'}
            </Link>
          )}
          {canDance && (
            <Link to={`/play/${duel.challenge.slug}?mode=practice`} className="small">
              Practice first
            </Link>
          )}
        </section>
      )}

      {duel.status === 'expired' && <p className="muted">This duel expired before both attempts were finished. Ratings were not changed.</p>}
      {duel.status === 'declined' && <p className="muted">This duel was declined.</p>}

      {duel.outcome && (
        <section className="stack">
          <h2 className="center-text">
            {duel.outcome.is_draw ? "It's a draw" : `${duel.outcome.winner} wins`}
          </h2>
          <div className="grid-2">
            {(['challenger', 'opponent'] as const).map((side) => {
              const s = duel.outcome![side]
              const u = duel[side]
              const mine = duel.role === side
              return (
                <div key={side} className={`card ${duel.outcome!.winner === u.username ? 'winner' : ''}`}>
                  <h3>
                    {u.username} {mine && <span className="muted small">(you)</span>}
                  </h3>
                  <div className="score-big">{fmtScore(s.total)}</div>
                  <ScoreBars result={s} />
                  <p className="muted small">
                    Rating {s.rating_delta >= 0 ? '+' : ''}
                    {s.rating_delta.toFixed(1)}
                  </p>
                  {mine && <Link to={`/attempts/${s.attempt_id}`}>See my feedback →</Link>}
                </div>
              )
            })}
          </div>
          <p className="muted small center-text">
            Differences of 2 points or less count as a draw. Scores are choreography-match estimates, not judgments of
            artistry.
          </p>
        </section>
      )}
    </div>
  )
}
