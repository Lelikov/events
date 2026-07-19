import { apiRequest } from '../shared/api.ts'
import type { LoginPayload, LoginResponse } from './types.ts'

export async function login(payload: LoginPayload): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/login', {
    method: 'POST',
    body: payload,
    auth: false,
  })
}
