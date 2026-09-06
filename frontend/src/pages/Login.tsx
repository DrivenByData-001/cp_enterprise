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
    <div className="app-shell" style={{ maxWidth: 360, marginTop: '15vh' }}>
      <div className="card">
        <h1 style={{ fontSize: 20, margin: '0 0 4px' }}>Career Navigator</h1>
        <p className="secondary" style={{ margin: '0 0 16px', fontSize: 13 }}>
          Private tool — sign in to continue.
        </p>
        <form onSubmit={handleSubmit}>
          <input
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            disabled={submitting}
            style={{ width: '100%', marginBottom: 10 }}
          />
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
