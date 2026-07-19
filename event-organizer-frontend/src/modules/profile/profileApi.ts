import { apiRequest } from '../shared/api.ts'
import type { Profile } from './types.ts'

export async function getProfile(): Promise<Profile> {
  return apiRequest<Profile>('/api/me/profile')
}

export async function updateProfile(body: { name: string; time_zone: string }): Promise<Profile> {
  return apiRequest<Profile>('/api/me/profile', { method: 'PUT', body })
}

export async function changePassword(body: { old_password: string; new_password: string }): Promise<void> {
  await apiRequest<void>('/api/me/password', { method: 'PUT', body })
}
