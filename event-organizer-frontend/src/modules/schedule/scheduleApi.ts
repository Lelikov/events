import { ApiError, apiRequest } from '../shared/api.ts'
import type { ScheduleBundle, TravelBody, UpsertScheduleBody } from './types.ts'

// 404 = the organizer has no schedule yet → empty editor (not an error).
export async function getSchedule(): Promise<ScheduleBundle | null> {
  try {
    return await apiRequest<ScheduleBundle>('/api/me/schedule')
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export async function putSchedule(body: UpsertScheduleBody): Promise<ScheduleBundle> {
  return apiRequest<ScheduleBundle>('/api/me/schedule', { method: 'PUT', body })
}

export async function putTravel(body: TravelBody): Promise<unknown> {
  return apiRequest<unknown>('/api/me/schedule/travel', { method: 'PUT', body })
}
