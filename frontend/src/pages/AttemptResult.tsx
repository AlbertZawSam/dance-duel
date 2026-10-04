import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type Attempt } from '../api'
import { ErrorBox, ScoreDetail, Spinner } from '../components'

export default function AttemptResult() {
  const { id = '' } = useParams()
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stop = false
    const poll = async () => {
      try {
        const a = await api.attempt(id)
        if (stop) return
        setAttempt(a)
        if (['queued', 'processing'].includes(a.status)) setTimeout(poll, 2000)
      } catch (e) {
        setError((e as Error).message)
      }
    }
    poll()
    return () => {
      stop = true
    }
  }, [id])

  if (error) return <ErrorBox error={error} />
  if (!attempt) return <Spinner />

  const back = attempt.duel_id ? `/duels/${attempt.duel_id}` : `/challenges/${attempt.challenge_slug}`
  const retry = attempt.duel_id
    ? `/play/${attempt.challenge_slug}?mode=official&duel=${attempt.duel_id}`
    : `/play/${attempt.challenge_slug}?mode=practice`

  return (
    <div className="stack narrow">
      <p>
        <Link to={back}>← Back</Link>
      </p>
      <h1>{attempt.mode === 'official' ? 'Official attempt' : 'Practice result'}</h1>

      {['queued', 'processing'].includes(attempt.status) && <Spinner label="Analyzing…" />}

      {attempt.status === 'invalid' && (
        <div className="card stack">
          <h3>We couldn't score this recording</h3>
          <p>{attempt.invalid_detail}</p>
          <p className="muted small">
            This is a visibility problem, not a low score. It does not count as a loss.
          </p>
          <Link className="button" to={retry}>
            Try again
          </Link>
        </div>
      )}

      {attempt.status === 'technical_failure' && (
        <div className="card stack">
          <h3>Something went wrong on our side</h3>
          <p className="muted">The analysis failed after several tries. This does not count against you.</p>
          <Link className="button" to={retry}>
            Try again
          </Link>
        </div>
      )}

      {attempt.status === 'scored' && attempt.result_hidden && (
        <div className="card stack">
          <h3>Submitted ✓</h3>
          <p>Your attempt was scored. Results are revealed when your opponent finishes too.</p>
          <Link to={back}>Go to the duel</Link>
        </div>
      )}

      {attempt.result && <ScoreDetail result={attempt.result} />}
    </div>
  )
}
