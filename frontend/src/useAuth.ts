import { createContext, useContext } from 'react'

export const AuthContext = createContext<{ logout: () => void } | null>(null)

/** Nav-bar "log out" button (App.tsx) reads this — provided by <AuthGate>. */
export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthGate>')
  return ctx
}
