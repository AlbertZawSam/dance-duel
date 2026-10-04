import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, tokenStore, type User } from './api'

type AuthState = {
  user: User | null
  loading: boolean
  signIn: (username: string, password: string, register: boolean) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!tokenStore.get()) {
      setUser(null)
      return
    }
    try {
      setUser(await api.me())
    } catch {
      tokenStore.set(null)
      setUser(null)
    }
  }, [])

  useEffect(() => {
    refresh().finally(() => setLoading(false))
    const onUnauthorized = () => {
      tokenStore.set(null)
      setUser(null)
    }
    window.addEventListener('dd:unauthorized', onUnauthorized)
    return () => window.removeEventListener('dd:unauthorized', onUnauthorized)
  }, [refresh])

  const signIn = async (username: string, password: string, register: boolean) => {
    const res = register ? await api.register(username, password) : await api.login(username, password)
    tokenStore.set(res.token)
    setUser(res.user)
  }

  const signOut = async () => {
    try {
      await api.logout()
    } finally {
      tokenStore.set(null)
      setUser(null)
    }
  }

  return <AuthContext.Provider value={{ user, loading, signIn, signOut, refresh }}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth outside AuthProvider')
  return ctx
}
