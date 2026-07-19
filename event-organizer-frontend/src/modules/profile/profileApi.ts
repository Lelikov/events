import { apiRequest } from '../shared/api.ts'
import type { Profile } from './types.ts'

export async function getProfile(): Promise<Profile> {
  return apiRequest<Profile>('/api/me/profile')
}
