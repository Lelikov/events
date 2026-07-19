import { getJwtToken, removeJwtToken } from '../auth/storage.ts'
import { getEnv } from './runtimeEnv.ts'

// Runtime window._env_ (written by docker-entrypoint.d/40-env-config.sh) wins over
// the build-time value, so one image serves every environment. getEnv falls back
// to import.meta.env when no runtime value is present (dev + tests).
const API_BASE_URL = getEnv('VITE_API_BASE_URL')

if (!import.meta.env.DEV && !API_BASE_URL) {
  console.warn(
    'VITE_API_BASE_URL is empty: API requests will be sent relative to the static host. ' +
      'Set VITE_API_BASE_URL at build time unless the SPA is served behind the same origin as event-organizer.',
  )
}

export class ApiError extends Error {
  status: number
  code: string | null
  details: unknown

  constructor(message: string, status: number, details: unknown, code: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

type ErrorDetail = { code: string | null; message: string | null }

// FastAPI HTTPException returns detail as a plain string; a structured
// {code, message} detail is also tolerated.
function parseErrorDetail(payload: unknown): ErrorDetail {
  if (typeof payload !== 'object' || payload === null || !('detail' in payload)) {
    return { code: null, message: null }
  }
  const detail = (payload as { detail: unknown }).detail
  if (typeof detail === 'string') {
    return { code: null, message: detail }
  }
  if (typeof detail === 'object' && detail !== null) {
    const structured = detail as { code?: unknown; message?: unknown }
    return {
      code: typeof structured.code === 'string' ? structured.code : null,
      message: typeof structured.message === 'string' ? structured.message : null,
    }
  }
  return { code: null, message: null }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  auth?: boolean
  baseUrl?: string
  // Opt out of the global "401 on a token-carrying request → clear session +
  // redirect to /login" behavior. Needed by endpoints where a 401 means
  // something other than an expired/revoked JWT (e.g. PUT /api/me/password
  // returns 401 for a wrong current password, not a stale session) — the
  // caller still gets the thrown ApiError(401), it just isn't logged out.
  // Defaults to false so every other caller keeps today's behavior.
  suppressAuthRedirect?: boolean
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true, baseUrl = API_BASE_URL, suppressAuthRedirect = false } = options
  const headers: Record<string, string> = {
    Accept: 'application/json',
  }

  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  let tokenAttached = false
  if (auth) {
    const token = getJwtToken()
    if (token) {
      headers.Authorization = `Bearer ${token}`
      tokenAttached = true
    }
  }

  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (response.status === 204) {
    return null as T
  }

  const contentType = response.headers.get('content-type')
  const isJson = contentType?.includes('application/json')
  const payload = isJson ? await response.json() : await response.text()

  if (!response.ok) {
    const detail = parseErrorDetail(payload)
    const message = detail.message ?? `Ошибка запроса (${response.status})`
    const error = new ApiError(message, response.status, payload, detail.code)
    // A 401 on a request that carried a token means the JWT is expired or
    // revoked: clear the session and force a re-login. Requests without a
    // token (POST /auth/login itself) must NOT redirect.
    if (error.status === 401 && tokenAttached && !suppressAuthRedirect) {
      removeJwtToken()
      window.location.href = '/login'
    }
    throw error
  }

  return payload as T
}
