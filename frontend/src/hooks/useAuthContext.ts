import { useContext } from 'react'
import { AuthContext } from '../context/auth-context'
import type { AuthContextValue } from './useAuth'

export function useAuthContext(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) {
    throw new Error('useAuthContext must be used within AuthProvider')
  }
  return value
}
