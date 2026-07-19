import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../shared/api.ts'
import { changePassword, getProfile, updateProfile } from './profileApi.ts'

afterEach(() => vi.restoreAllMocks())

describe('profileApi', () => {
  it('updateProfile sends only name + time_zone', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue({ name: 'N', email: 'e@x.io', time_zone: 'UTC' })
    await updateProfile({ name: 'N', time_zone: 'UTC' })
    expect(spy).toHaveBeenCalledWith('/api/me/profile', { method: 'PUT', body: { name: 'N', time_zone: 'UTC' } })
  })

  it('changePassword PUTs old + new', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue(null)
    await changePassword({ old_password: 'a', new_password: 'b' })
    expect(spy).toHaveBeenCalledWith('/api/me/password', { method: 'PUT', body: { old_password: 'a', new_password: 'b' } })
  })

  it('getProfile GETs the profile', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue({ name: null, email: 'e@x.io', time_zone: null })
    await getProfile()
    expect(spy).toHaveBeenCalledWith('/api/me/profile')
  })
})
