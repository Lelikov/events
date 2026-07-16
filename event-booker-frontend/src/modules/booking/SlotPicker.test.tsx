import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { SlotPicker } from './SlotPicker.tsx'
import { dateKey, startOfMonth } from './calendar.ts'
import type { Slots } from './types.ts'

vi.mock('./bookerApi.ts', () => ({ getSlots: vi.fn() }))
import { getSlots } from './bookerApi.ts'

let container: HTMLDivElement
let root: Root

function futureDay(offset: number): Date {
  const t = new Date()
  return new Date(t.getFullYear(), t.getMonth(), t.getDate() + offset)
}

async function mount(slots: Slots, onSelectSlot = vi.fn(), initialMonth?: Date) {
  vi.mocked(getSlots).mockResolvedValue(slots)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(
      <SlotPicker
        eventTypeId="e1"
        eventTitle="Тест"
        durationMinutes={30}
        timeZone="UTC"
        onTimeZoneChange={vi.fn()}
        onSelectSlot={onSelectSlot}
        initialMonth={initialMonth}
      />,
    )
  })
  await act(async () => {})
  return { onSelectSlot }
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

const slots = (map: Record<string, string[]>): Slots => ({ event_type_id: 'e1', time_zone: 'UTC', slots: map })

describe('SlotPicker (calendar)', () => {
  it('auto-selects the first available day and lists its slots', async () => {
    const day = futureDay(2)
    const key = dateKey(day)
    const iso = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 9, 0).toISOString()
    const { onSelectSlot } = await mount(slots({ [key]: [iso] }), vi.fn(), startOfMonth(day))
    const buttons = container.querySelectorAll('.slot-button')
    expect(buttons.length).toBe(1)
    await act(async () => (buttons[0] as HTMLButtonElement).click())
    expect(onSelectSlot).toHaveBeenCalledWith(iso)
  })

  it('shows an empty message when the month has no slots', async () => {
    await mount(slots({}))
    expect(container.textContent).toContain('Нет свободных слотов')
    expect(container.querySelectorAll('.slot-button').length).toBe(0)
  })

  it('refetches when the month is changed', async () => {
    const day = futureDay(2)
    await mount(slots({ [dateKey(day)]: [new Date().toISOString()] }), vi.fn(), startOfMonth(day))
    expect(vi.mocked(getSlots)).toHaveBeenCalledTimes(1)
    const next = container.querySelector('.rdp-nav')!.querySelectorAll('button')
    await act(async () => (next[next.length - 1] as HTMLButtonElement).click())
    expect(vi.mocked(getSlots).mock.calls.length).toBeGreaterThanOrEqual(2)
  })
})
