import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { BookingsPage } from './BookingsPage.tsx'
import * as bookingsApi from './bookingsApi.ts'
import * as profileApi from '../profile/profileApi.ts'
import type { BookingRow } from './types.ts'

let container: HTMLDivElement
let root: Root

const future = new Date(Date.now() + 86_400_000).toISOString()
const futureEnd = new Date(Date.now() + 90_000_000).toISOString()
const past = new Date(Date.now() - 86_400_000).toISOString()
const pastEnd = new Date(Date.now() - 82_800_000).toISOString()

async function mount(rows: BookingRow[]) {
  vi.spyOn(bookingsApi, 'getBookings').mockResolvedValue(rows)
  vi.spyOn(profileApi, 'getProfile').mockResolvedValue({ name: 'N', email: 'e@x.io', time_zone: 'UTC' })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<BookingsPage />))
  await act(async () => {})
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

describe('BookingsPage', () => {
  it('splits upcoming and past', async () => {
    await mount([
      { id: 'a', start_time: future, end_time: futureEnd, status: 'confirmed' },
      { id: 'b', start_time: past, end_time: pastEnd, status: 'cancelled' },
    ])
    const groups = container.querySelectorAll('.booking-group')
    expect(groups).toHaveLength(2)
    expect(groups[0].querySelectorAll('.booking-row')).toHaveLength(1) // upcoming
    expect(groups[1].querySelectorAll('.booking-row')).toHaveLength(1) // past
    expect(container.querySelector('.badge--confirmed')).toBeTruthy()
  })

  it('shows an empty state when there are none', async () => {
    await mount([])
    expect(container.querySelector('.empty-state')).toBeTruthy()
  })
})
