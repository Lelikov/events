const TOKEN_STORAGE_KEY = 'event_organizer_jwt'

// The JWT lives in sessionStorage (tab-scoped, dropped when the tab closes),
// narrowing the theft window. One-time cleanup of any token an older build may
// have left in localStorage.
localStorage.removeItem(TOKEN_STORAGE_KEY)

export function getJwtToken(): string | null {
  return sessionStorage.getItem(TOKEN_STORAGE_KEY)
}

export function setJwtToken(token: string): void {
  sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
}

export function removeJwtToken(): void {
  sessionStorage.removeItem(TOKEN_STORAGE_KEY)
}
