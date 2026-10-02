import { createContext } from 'react'
import type { AuthContextValue } from '../hooks/useAuth'

export const AuthContext = createContext<AuthContextValue | null>(null)
