import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { AuthContext, type AuthContextValue } from './context.ts'
import { isTokenExpired } from './jwt.ts'
import { getJwtToken, removeJwtToken, setJwtToken } from './storage.ts'

type AuthProviderProps = {
  children: ReactNode
}

function getValidStoredToken(): string | null {
  const token = getJwtToken()
  if (token && isTokenExpired(token)) {
    removeJwtToken()
    return null
  }
  return token
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [jwtToken, setJwtTokenState] = useState<string | null>(() => getValidStoredToken())

  const loginWithToken = useCallback((token: string) => {
    setJwtToken(token)
    setJwtTokenState(token)
  }, [])

  // The BFF has no logout endpoint — logout is purely local: clear storage + state.
  const logout = useCallback(() => {
    removeJwtToken()
    setJwtTokenState(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      isAuthenticated: Boolean(jwtToken),
      jwtToken,
      loginWithToken,
      logout,
    }),
    [jwtToken, loginWithToken, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
