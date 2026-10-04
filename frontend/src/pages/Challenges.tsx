import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmtTime, type Attempt, type Challenge, type User } from '../api'
import { ErrorBox, Spinner } from '../components'

export function ChallengeList() {
  const [items, setItems] = useState<Challenge[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.challenges().then(setItems).catch((e) => setError(e.message))
  }, [])
  if (error) return <ErrorBox error={error} />
  if (!items) return <Spinner />
  return (
    <div className="stack">
      <h1>Challenges</h1>
      <div className="cards">
        {items.map((c) => (
          <Link key={c.id} to={`/challenges/${c.slug}`} className="card challenge-card">
            <h3>{c.title}</h3>
            <p className="muted">{c.description}</p>
            <span className="pill">{c.difficulty}</span>{' '}
            <span className="muted small">{fmtTime(c.current_version?.duration_s ?? 0)}</span>
          </Link>
        ))}
      </div>
    </div>
  )
}

export function ChallengeDetail() {
  const { slug = '' } = useParams()
  const navigate = useNavigate()
  const [challenge, setChallenge] = useState<Challenge | null>(null)
  const [practice, setPractice] = useState<Attempt[]>([])
  const [opponent, setOpponent] = useState('')
  const [suggestions, setSuggestions] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.challenge(slug).then(setChallenge).catch((e) => setError(e.message))
    api
      .attempts('practice')
      .then((all) => setPractice(all.filter((a) => a.challenge_slug === slug).slice(0, 5)))
      .catch(() => {})
  }, [slug])

  useEffect(() => {
    if (opponent.length < 1) {
      setSuggestions([])
      return
    }
    const id = setTimeout(() => api.searchUsers(opponent).then(setSuggestions).catch(() => {}), 200)
    return () => clearTimeout(id)
  }, [opponent])

  const invite = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const d = await api.createDuel(opponent.trim(), slug)
      navigate(`/duels/${d.id}`)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!challenge) return error ? <ErrorBox error={error} /> : <Spinner />
  const v = challenge.current_version!

  return (
    <div className="stack">
      <div>
        <h1>{challenge.title}</h1>
        <p className="muted">{challenge.description}</p>
        <span className="pill">{challenge.difficulty}</span>{' '}
        <span className="muted small">
          {fmtTime(v.duration_s)} · version {v.version} · {v.mirror_policy === 'mirror_player' ? 'mirrored' : 'same-side'}{' '}
          orientation
        </span>
      </div>

      <div className="grid-2">
        <section className="card stack">
          <h3>Practice</h3>
          <p className="muted">
            Learn the routine{v.has_practice_reference ? ' with the reference guide' : ''}, then record unranked runs to get
            feedback. Practice never affects your record.
          </p>
          <Link className="button" to={`/play/${slug}?mode=practice`}>
            Practice
          </Link>
          {practice.length > 0 && (
            <ul className="list small">
              {practice.map((a) => (
                <li key={a.id}>
                  <Link to={`/attempts/${a.id}`}>
                    {new Date(a.created_at).toLocaleString()} —{' '}
                    {a.status === 'scored' ? a.result?.total.toFixed(1) : a.status.replace('_', ' ')}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card">
          <form className="stack" onSubmit={invite}>
            <h3>Challenge a friend</h3>
            <p className="muted">
              Both of you dance this exact version from memory, whenever suits you within 48 hours. Scores are revealed
              once you both finish.
            </p>
            <label>
              Opponent username
              <input value={opponent} onChange={(e) => setOpponent(e.target.value)} list="players" required />
              <datalist id="players">
                {suggestions.map((u) => (
                  <option key={u.id} value={u.username} />
                ))}
              </datalist>
            </label>
            <ErrorBox error={error} />
            <button className="hot" disabled={busy}>
              Send challenge
            </button>
          </form>
        </section>
      </div>

      <p className="muted small">
        Assets: {v.license_note} · Pose model: {v.pose_model}
      </p>
    </div>
  )
}
