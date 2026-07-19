export type JwtPayload = {
  sub?: string
  exp?: number
}

/**
 * Decodes a JWT payload without verifying the signature (the BFF verifies; the
 * client only needs claims for UX like expiry + the sidebar identity). Handles
 * base64url and returns null for malformed tokens.
 */
export function decodeJwtPayload(token: string): JwtPayload | null {
  const part = token.split('.')[1]
  if (!part) return null
  const base64 = part.replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
  try {
    const parsed: unknown = JSON.parse(atob(padded))
    if (typeof parsed !== 'object' || parsed === null) return null
    return parsed as JwtPayload
  } catch {
    return null
  }
}

/**
 * True when the token carries an `exp` claim already in the past. Undecodable
 * tokens or tokens without `exp` are treated as not expired: the BFF rejects
 * them with 401 and the apiRequest interceptor handles it.
 */
export function isTokenExpired(token: string): boolean {
  const payload = decodeJwtPayload(token)
  if (!payload || typeof payload.exp !== 'number') return false
  return payload.exp * 1000 <= Date.now()
}
