import { useEffect, useState, type ReactNode } from 'react'
import { api } from './lib/api'
import Login from './pages/Login'
import { AuthContext } from './useAuth'

type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

/**
 * Gatekeeper for the whole app (see main.tsx): shows the login screen until
 * a valid session exists, then renders `children` (the real app) inside an
 * AuthContext that exposes `logout`. Also registers itself as the target of
 * api.ts's 401 hook, so any API call anywhere in the app that comes back
 * "session expired" drops straight back to the login screen — see
 * docs/20-render-deployment.md "Frontend login/session behaviour".
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')

  useEffect(() => {
    api.setUnauthorizedHandler(() => setStatus('unauthenticated'))
    api
      .authStatus()
      .then((authenticated) => setStatus(authenticated ? 'authenticated' : 'unauthenticated'))
      .catch(() => setStatus('unauthenticated'))
    return () => api.setUnauthorizedHandler(null)
  }, [])

  if (status === 'loading') {
    return (
      <div className="app-shell">
        <p className="muted">Loading…</p>
      </div>
    )
  }

  if (status === 'unauthenticated') {
    return <Login onSuccess={() => setStatus('authenticated')} />
  }

  const logout = () => {
    api.logout().finally(() => setStatus('unauthenticated'))
  }

  return <AuthContext.Provider value={{ logout }}>{children}</AuthContext.Provider>
}
