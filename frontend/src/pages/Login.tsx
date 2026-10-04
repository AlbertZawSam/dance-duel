import { useState, type FormEvent } from 'react'
import { useAuth } from '../auth'
import { ErrorBox } from '../components'

export default function Login() {
  const { signIn } = useAuth()
  const [register, setRegister] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(username.trim(), password, register)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <div className="brand-big">
        Dance<span>Duel</span>
      </div>
      <p className="muted">Learn the routine. Then dance it from memory, with only the song playing.</p>
      <form className="card stack" onSubmit={submit}>
        <h2>{register ? 'Create an account' : 'Sign in'}</h2>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={register ? 'new-password' : 'current-password'}
            minLength={8}
            required
          />
        </label>
        <ErrorBox error={error} />
        <button disabled={busy}>{busy ? '…' : register ? 'Create account' : 'Sign in'}</button>
        <button type="button" className="link" onClick={() => setRegister((r) => !r)}>
          {register ? 'I already have an account' : 'New here? Create an account'}
        </button>
      </form>
    </div>
  )
}
