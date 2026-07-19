import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../shared/api.ts'
import { ApiError } from '../shared/api.ts'
import { getSchedule, putSchedule, putTravel } from './scheduleApi.ts'

afterEach(() => vi.restoreAllMocks())

describe('scheduleApi', () => {
  it('getSchedule returns the bundle', async () => {
    const bundle = { schedule: { id: '1', owner_user_id: '2', name: 'N', time_zone: 'UTC' }, weekly_hours: [], date_overrides: [], travel_schedules: [] }
    vi.spyOn(api, 'apiRequest').mockResolvedValue(bundle)
    await expect(getSchedule()).resolves.toEqual(bundle)
  })

  it('getSchedule returns null on 404', async () => {
    vi.spyOn(api, 'apiRequest').mockRejectedValue(new ApiError('nope', 404, null))
    await expect(getSchedule()).resolves.toBeNull()
  })

  it('getSchedule rethrows non-404 errors', async () => {
    vi.spyOn(api, 'apiRequest').mockRejectedValue(new ApiError('boom', 502, null))
    await expect(getSchedule()).rejects.toBeInstanceOf(ApiError)
  })

  it('putSchedule PUTs the body', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue({})
    const body = { name: 'N', time_zone: 'UTC', weekly_hours: [], date_overrides: [] }
    await putSchedule(body)
    expect(spy).toHaveBeenCalledWith('/api/me/schedule', { method: 'PUT', body })
  })

  it('putTravel PUTs the travel envelope', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue({})
    const body = { travel_schedules: [] }
    await putTravel(body)
    expect(spy).toHaveBeenCalledWith('/api/me/schedule/travel', { method: 'PUT', body })
  })
})
