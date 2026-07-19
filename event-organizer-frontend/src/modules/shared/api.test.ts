import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest, ApiError } from './api.ts'
import { getJwtToken, setJwtToken } from '../auth/storage.ts'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

beforeEach(() => {
  sessionStorage.clear()
  setJwtToken('a-token')
  window.history.pushState({}, '', '/schedule')
})

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
  window.history.pushState({}, '', '/')
})

describe('apiRequest — 401 handling', () => {
  it('suppressAuthRedirect: true — throws ApiError(401) but keeps the token and does not redirect', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(401, { detail: 'Invalid current password' }))

    await expect(
      apiRequest('/api/me/password', {
        method: 'PUT',
        body: { old_password: 'wrong', new_password: 'new' },
        suppressAuthRedirect: true,
      }),
    ).rejects.toMatchObject({ status: 401 })

    expect(getJwtToken()).toBe('a-token')
    expect(window.location.pathname).toBe('/schedule')
  })

  it('default (no flag) — clears the token on a token-carrying 401', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(401, { detail: 'Token expired' }))

    await expect(apiRequest('/api/me/profile')).rejects.toBeInstanceOf(ApiError)

    expect(getJwtToken()).toBeNull()
  })
})
