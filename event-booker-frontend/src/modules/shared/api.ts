const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  status: number
  details: unknown
  constructor(message: string, status: number, details: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

function parseDetailMessage(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null || !('detail' in payload)) {
    return null
  }
  const detail = (payload as { detail: unknown }).detail
  return typeof detail === 'string' ? detail : null
}

type RequestOptions = { method?: 'GET' | 'POST'; body?: unknown }

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body } = options
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const contentType = response.headers.get('content-type')
  const isJson = contentType?.includes('application/json')
  const payload = isJson ? await response.json() : await response.text()
  if (!response.ok) {
    const message = parseDetailMessage(payload) ?? `Ошибка запроса (${response.status})`
    throw new ApiError(message, response.status, payload)
  }
  return payload as T
}
