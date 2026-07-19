import { apiRequest } from '../shared/api.ts'
import type { Profile } from './types.ts'

export async function getProfile(): Promise<Profile> {
  return apiRequest<Profile>('/api/me/profile')
}

export async function updateProfile(body: { name: string; time_zone: string }): Promise<Profile> {
  return apiRequest<Profile>('/api/me/profile', { method: 'PUT', body })
}

export async function changePassword(body: { old_password: string; new_password: string }): Promise<void> {
  // 401 here means "wrong current password", not an expired session — suppress
  // the global auth-redirect so ProfilePage can show the real message instead
  // of the user being silently logged out.
  await apiRequest<void>('/api/me/password', { method: 'PUT', body, suppressAuthRedirect: true })
}
