import { createContext } from 'react'

export type AuthContextValue = {
  isAuthenticated: boolean
  jwtToken: string | null
  loginWithToken: (token: string) => void
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)
