import { useState, type FormEvent } from 'react'
import { api } from '../lib/api'

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!password || submitting) return
    setSubmitting(true)
    setError(null)
    const result = await api.login(password)
    setSubmitting(false)
    if (result.ok) {
      onSuccess()
    } else {
      setError(result.error)
      setPassword('')
    }
  }

  return (
    <div className="login-shell">
      <div className="card">
        <span className="brand-mark" aria-hidden="true">↗</span>
        <p className="eyebrow">Career Navigator</p>
        <h1>Welcome back.</h1>
        <p className="secondary">
          Your next chapter starts here. Sign in to your private career workspace.
        </p>
        <form onSubmit={handleSubmit}>
          <label>Password<input
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            disabled={submitting}
            style={{ width: '100%', marginBottom: 10 }}
          /></label>
          {error && (
            <p style={{ color: 'var(--critical)', fontSize: 13, margin: '0 0 10px' }} role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="primary" disabled={submitting || !password} style={{ width: '100%' }}>
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
