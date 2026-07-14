import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../shared/api.ts'
import { createBooking, getEventType, getSlots, listEventTypes } from './bookerApi.ts'

function mockFetch(status: number, jsonBody: unknown) {
  return vi.fn(async () =>
    new Response(JSON.stringify(jsonBody), {
      status,
      headers: { 'content-type': 'application/json' },
    }),
  )
}

afterEach(() => vi.restoreAllMocks())

describe('bookerApi', () => {
  it('listEventTypes unwraps items', async () => {
    const fetchMock = mockFetch(200, { items: [{ id: '1', slug: 's', title: 'T', duration_minutes: 30 }] })
    vi.stubGlobal('fetch', fetchMock)
    const out = await listEventTypes()
    expect(out).toEqual([{ id: '1', slug: 's', title: 'T', duration_minutes: 30 }])
    expect(fetchMock.mock.calls[0][0]).toBe('/api/public/event-types')
  })

  it('getEventType requests the id path', async () => {
    const fetchMock = mockFetch(200, { id: '42', slug: 's', title: 'T', duration_minutes: 60 })
    vi.stubGlobal('fetch', fetchMock)
    const out = await getEventType('42')
    expect(out.duration_minutes).toBe(60)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/public/event-types/42')
  })

  it('getSlots builds the query and returns slots', async () => {
    const fetchMock = mockFetch(200, { event_type_id: '1', time_zone: 'Europe/Moscow', slots: { '2026-10-01': ['2026-10-01T09:00:00Z'] } })
    vi.stubGlobal('fetch', fetchMock)
    const out = await getSlots('1', '2026-10-01T00:00:00Z', '2026-10-15T00:00:00Z', 'Europe/Moscow')
    expect(out.slots['2026-10-01']).toEqual(['2026-10-01T09:00:00Z'])
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/api/public/slots?')
    expect(url).toContain('event_type_id=1')
    expect(url).toContain('time_zone=Europe%2FMoscow')
  })

  it('createBooking POSTs the body and returns confirmation', async () => {
    const fetchMock = mockFetch(201, { booking_id: 'b1', event_type_title: 'T', start_time: 'x', end_time: 'y', status: 'confirmed', time_zone: 'UTC' })
    vi.stubGlobal('fetch', fetchMock)
    const out = await createBooking({ event_type_id: '1', name: 'A', email: 'a@b.io', start_time: 'x', time_zone: 'UTC' })
    expect(out.booking_id).toBe('b1')
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse((init as RequestInit).body as string)).toMatchObject({ email: 'a@b.io' })
  })

  it('maps a 409 to ApiError with the detail message', async () => {
    vi.stubGlobal('fetch', mockFetch(409, { detail: 'slot no longer available' }))
    await expect(
      createBooking({ event_type_id: '1', name: 'A', email: 'a@b.io', start_time: 'x', time_zone: 'UTC' }),
    ).rejects.toMatchObject({ status: 409, message: 'slot no longer available' })
    expect(ApiError).toBeDefined()
  })
})
