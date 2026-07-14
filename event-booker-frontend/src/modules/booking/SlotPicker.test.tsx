import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { SlotPicker } from './SlotPicker.tsx'

vi.mock('./bookerApi.ts', () => ({ getSlots: vi.fn() }))
import { getSlots } from './bookerApi.ts'

let container: HTMLDivElement
let root: Root

async function mount(onSelect = vi.fn(), onTimeZoneChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(
      <SlotPicker eventTypeId="e1" timeZone="UTC" onTimeZoneChange={onTimeZoneChange} onSelect={onSelect} />,
    )
  })
  await act(async () => {})
  return { onSelect, onTimeZoneChange }
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('SlotPicker', () => {
  it('renders slot times and reports the picked start_time', async () => {
    vi.mocked(getSlots).mockResolvedValue({
      event_type_id: 'e1',
      time_zone: 'UTC',
      slots: { '2026-10-01': ['2026-10-01T09:00:00Z', '2026-10-01T10:00:00Z'] },
    })
    const { onSelect } = await mount()
    const buttons = container.querySelectorAll('.slot-button')
    expect(buttons.length).toBe(2)
    await act(async () => (buttons[0] as HTMLButtonElement).click())
    expect(onSelect).toHaveBeenCalledWith('2026-10-01T09:00:00Z')
  })

  it('shows a message when there are no slots in the window', async () => {
    vi.mocked(getSlots).mockResolvedValue({ event_type_id: 'e1', time_zone: 'UTC', slots: {} })
    await mount()
    expect(container.textContent).toContain('Нет свободных слотов')
  })
})
