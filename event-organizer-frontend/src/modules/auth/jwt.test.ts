import { describe, expect, it } from 'vitest'
import { decodeJwtPayload, isTokenExpired } from './jwt.ts'

function makeToken(payload: object): string {
  const base64 = btoa(JSON.stringify(payload)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `header.${base64}.signature`
}

describe('decodeJwtPayload', () => {
  it('decodes a base64url payload', () => {
    const token = makeToken({ sub: 'a@b.c', exp: 123 })
    expect(decodeJwtPayload(token)).toEqual({ sub: 'a@b.c', exp: 123 })
  })

  it('returns null for a token without dots', () => {
    expect(decodeJwtPayload('not-a-jwt')).toBeNull()
  })

  it('returns null for garbage payloads', () => {
    expect(decodeJwtPayload('a.%%%%.c')).toBeNull()
    expect(decodeJwtPayload('')).toBeNull()
  })

  it('returns null for non-object payloads', () => {
    const base64 = btoa(JSON.stringify('just-a-string'))
    expect(decodeJwtPayload(`a.${base64}.c`)).toBeNull()
  })
})

describe('isTokenExpired', () => {
  it('is true when exp is in the past', () => {
    expect(isTokenExpired(makeToken({ exp: Math.floor(Date.now() / 1000) - 60 }))).toBe(true)
  })

  it('is false when exp is in the future', () => {
    expect(isTokenExpired(makeToken({ exp: Math.floor(Date.now() / 1000) + 3600 }))).toBe(false)
  })

  it('treats undecodable tokens as not expired', () => {
    expect(isTokenExpired('garbage')).toBe(false)
  })

  it('treats tokens without exp as not expired', () => {
    expect(isTokenExpired(makeToken({ sub: 'a@b.c' }))).toBe(false)
  })
})
