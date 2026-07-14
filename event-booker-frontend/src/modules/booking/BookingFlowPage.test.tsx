import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ApiError } from '../shared/api.ts'
import { BookingFlowPage } from './BookingFlowPage.tsx'

vi.mock('./bookerApi.ts', () => ({ getEventType: vi.fn(), getSlots: vi.fn(), createBooking: vi.fn() }))
vi.mock('../shared/routing.ts', () => ({ navigateTo: vi.fn() }))
import { createBooking, getEventType, getSlots } from './bookerApi.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<BookingFlowPage eventTypeId="e1" />)
  })
  await act(async () => {})
}

async function pickSlotAndFillForm() {
  const slot = container.querySelector('.slot-button') as HTMLButtonElement
  await act(async () => slot.click())
  const [name, email] = Array.from(container.querySelectorAll('input')) as HTMLInputElement[]
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(name, 'Анна')
    name.dispatchEvent(new Event('input', { bubbles: true }))
    setter.call(email, 'anna@example.com')
    email.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('BookingFlowPage', () => {
  it('walks slot → form → confirmation on success', async () => {
    vi.mocked(getEventType).mockResolvedValue({ id: 'e1', slug: 's', title: 'Знакомство', duration_minutes: 30 })
    vi.mocked(getSlots).mockResolvedValue({ event_type_id: 'e1', time_zone: 'UTC', slots: { '2026-10-01': ['2026-10-01T09:00:00Z'] } })
    vi.mocked(createBooking).mockResolvedValue({ booking_id: 'b1', event_type_title: 'Знакомство', start_time: '2026-10-01T09:00:00Z', end_time: '2026-10-01T09:30:00Z', status: 'confirmed', time_zone: 'UTC' })
    await mount()
    await pickSlotAndFillForm()
    await act(async () => {})
    expect(container.textContent).toContain('Встреча забронирована')
    expect(vi.mocked(createBooking).mock.calls[0][0]).toMatchObject({ event_type_id: 'e1', email: 'anna@example.com', start_time: '2026-10-01T09:00:00Z' })
  })

  it('returns to the slot step with a banner on 409', async () => {
    vi.mocked(getEventType).mockResolvedValue({ id: 'e1', slug: 's', title: 'Знакомство', duration_minutes: 30 })
    vi.mocked(getSlots).mockResolvedValue({ event_type_id: 'e1', time_zone: 'UTC', slots: { '2026-10-01': ['2026-10-01T09:00:00Z'] } })
    vi.mocked(createBooking).mockRejectedValue(new ApiError('slot no longer available', 409, {}))
    await mount()
    await pickSlotAndFillForm()
    await act(async () => {})
    expect(container.textContent).toContain('слот')
    expect(container.querySelector('.slot-button')).not.toBeNull()
  })
})
