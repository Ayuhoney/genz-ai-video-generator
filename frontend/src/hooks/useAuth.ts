import { useCallback, useEffect, useState } from 'react'
import type { User } from '../types'
import * as api from '../services/api'
import {
  clearAuthSession,
  getToken,
  readStoredUser,
  setAuthSession,
} from '../services/client'

export function useAuth() {
  const [user, setUser] = useState<User | null>(() => readStoredUser<User>())
  const [isLoading, setIsLoading] = useState(() => Boolean(getToken()))
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = getToken()
    if (!token) {
      return
    }

    let cancelled = false
    void api
      .getMe()
      .then((nextUser) => {
        if (cancelled) {
          return
        }
        setAuthSession(token, nextUser)
        setUser(nextUser)
        setIsLoading(false)
      })
      .catch(() => {
        if (cancelled) {
          return
        }
        clearAuthSession()
        setUser(null)
        setIsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await api.login({ email, password })
      setUser(data.user)
      setIsLoading(false)
      return data.user
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Unable to sign in.'
      setError(message)
      setIsLoading(false)
      throw err
    }
  }, [])

  const register = useCallback(
    async (email: string, password: string, name: string) => {
      setIsLoading(true)
      setError(null)
      try {
        const data = await api.register({ email, password, name })
        setUser(data.user)
        setIsLoading(false)
        return data.user
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Unable to register.'
        setError(message)
        setIsLoading(false)
        throw err
      }
    },
    [],
  )

  const logout = useCallback(() => {
    clearAuthSession()
    setUser(null)
    setError(null)
  }, [])

  return {
    user,
    isAuthenticated: Boolean(user) && Boolean(getToken()),
    isLoading,
    error,
    login,
    register,
    logout,
  }
}

export type AuthContextValue = ReturnType<typeof useAuth>
