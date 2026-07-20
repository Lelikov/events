import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { BookingDetailPanel } from './BookingDetailPanel.tsx'
import * as api from './bookingsApi.ts'
import type { BookingDetail } from './types.ts'

const detail: BookingDetail = {
  id: 'b1',
  title: 'Консультация',
  start_time: '2026-10-01T09:00:00Z',
  end_time: '2026-10-01T09:30:00Z',
  status: 'confirmed',
  client_name: 'Анна',
  client_email: 'anna@x.io',
  client_time_zone: 'Europe/Berlin',
  created_at: '2026-09-01T08:00:00Z',
  field_answers: [{ label: 'Комментарий', value: 'привет' }],
}

let container: HTMLDivElement
let root: Root

async function mount(bookingId: string | null) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<BookingDetailPanel bookingId={bookingId} organizerTz="Europe/Moscow" />))
  await act(async () => {})
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

describe('BookingDetailPanel', () => {
  it('shows a placeholder when nothing is selected', async () => {
    await mount(null)
    expect(container.querySelector('.detail-empty')).not.toBeNull()
  })

  it('renders the fetched booking detail', async () => {
    vi.spyOn(api, 'getBookingDetail').mockResolvedValue(detail)
    await mount('b1')
    expect(container.textContent).toContain('Консультация')
    expect(container.textContent).toContain('Анна')
    expect(container.textContent).toContain('anna@x.io')
    expect(container.textContent).toContain('Комментарий')
  })

  it('shows an error when the fetch fails', async () => {
    vi.spyOn(api, 'getBookingDetail').mockRejectedValue(new Error('nope'))
    await mount('b1')
    expect(container.querySelector('.error-text')).not.toBeNull()
  })
})
