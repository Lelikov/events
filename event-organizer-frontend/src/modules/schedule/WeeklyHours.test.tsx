import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { WeeklyHours } from './WeeklyHours.tsx'
import { emptyDays, type DayState } from './schedule.ts'

let container: HTMLDivElement
let root: Root

async function mount(days: DayState[], onChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<WeeklyHours days={days} onChange={onChange} />))
  return onChange
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('WeeklyHours', () => {
  it('renders 7 weekday rows', async () => {
    await mount(emptyDays())
    expect(container.querySelectorAll('.weekday-row')).toHaveLength(7)
    expect(container.querySelectorAll('.weekday-name')[0].textContent).toBe('Пн')
    expect(container.querySelectorAll('.weekday-name')[6].textContent).toBe('Вс')
  })

  it('enabling a day adds a default interval', async () => {
    const onChange = await mount(emptyDays())
    const toggle = container.querySelector('.weekday-row input[type="checkbox"]') as HTMLInputElement
    await act(async () => toggle.click())
    const next = onChange.mock.calls[0][0] as DayState[]
    expect(next[0].enabled).toBe(true)
    expect(next[0].intervals).toHaveLength(1)
  })

  it('adds an interval on an enabled day', async () => {
    const days = emptyDays()
    days[0] = { enabled: true, intervals: [{ start: '09:00', end: '12:00' }] }
    const onChange = await mount(days)
    const addBtn = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('интервал'))!
    await act(async () => addBtn.click())
    expect((onChange.mock.calls[0][0] as DayState[])[0].intervals).toHaveLength(2)
  })

  it('removes an interval on an enabled day with multiple intervals', async () => {
    const days = emptyDays()
    days[0] = {
      enabled: true,
      intervals: [
        { start: '09:00', end: '12:00' },
        { start: '14:00', end: '18:00' },
      ],
    }
    const onChange = await mount(days)
    const removeButtons = container.querySelectorAll('button[aria-label="Удалить интервал"]')
    expect(removeButtons).toHaveLength(2)
    await act(async () => (removeButtons[0] as HTMLButtonElement).click())
    const next = onChange.mock.calls[0][0] as DayState[]
    expect(next[0].enabled).toBe(true)
    expect(next[0].intervals).toHaveLength(1)
    expect(next[0].intervals[0]).toEqual({ start: '14:00', end: '18:00' })
  })

  it('removing the last interval leaves the day enabled with zero intervals', async () => {
    const days = emptyDays()
    days[0] = { enabled: true, intervals: [{ start: '09:00', end: '12:00' }] }
    const onChange = await mount(days)
    const removeButton = container.querySelector('button[aria-label="Удалить интервал"]') as HTMLButtonElement
    await act(async () => removeButton.click())
    const next = onChange.mock.calls[0][0] as DayState[]
    expect(next[0].enabled).toBe(true)
    expect(next[0].intervals).toHaveLength(0)
  })
})
