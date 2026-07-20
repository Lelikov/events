import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { SchedulePage } from './SchedulePage.tsx'
import * as scheduleApi from './scheduleApi.ts'
import type { ScheduleBundle } from './types.ts'

let container: HTMLDivElement
let root: Root

const bundle: ScheduleBundle = {
  schedule: { id: '1', owner_user_id: '2', name: 'Моё', time_zone: 'Europe/Moscow' },
  weekly_hours: [{ day_of_week: 1, start_time: '09:00:00', end_time: '12:00:00' }],
  date_overrides: [],
  travel_schedules: [],
}

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<SchedulePage />))
  await act(async () => {}) // flush the load effect
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

describe('SchedulePage', () => {
  it('loads the bundle into rows', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    await mount()
    expect(container.querySelectorAll('.weekday-row')).toHaveLength(7)
    const mon = container.querySelector('.weekday-row input[type="checkbox"]') as HTMLInputElement
    expect(mon.checked).toBe(true)
  })

  it('starts empty on 404', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(null)
    await mount()
    const boxes = [...container.querySelectorAll('.weekday-row input[type="checkbox"]')] as HTMLInputElement[]
    expect(boxes.every((b) => !b.checked)).toBe(true)
  })

  it('shows a single save button labelled Сохранить', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    await mount()
    const labels = [...container.querySelectorAll('button')].map((b) => b.textContent)
    expect(labels.filter((t) => t?.includes('Сохранить'))).toEqual(['Сохранить'])
  })

  it('marks a section dirty and enables save after an edit', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    await mount()
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить') as HTMLButtonElement
    expect(save.disabled).toBe(true)
    const mon = container.querySelector('.weekday-row input[type="checkbox"]') as HTMLInputElement
    await act(async () => mon.click()) // toggle Пн off → weekly section dirty
    expect(container.querySelector('.section.is-dirty')).not.toBeNull()
    expect(save.disabled).toBe(false)
  })

  it('saves the schedule (incl. name) after an edit', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    const put = vi.spyOn(scheduleApi, 'putSchedule').mockResolvedValue(bundle)
    await mount()
    const mon = container.querySelector('.weekday-row input[type="checkbox"]') as HTMLInputElement
    await act(async () => mon.click()) // toggle Пн off → weekly_hours becomes []
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить')!
    await act(async () => save.click())
    expect(put).toHaveBeenCalledWith({
      name: 'Моё',
      time_zone: 'Europe/Moscow',
      weekly_hours: [],
      date_overrides: [],
    })
  })

  it('saves only travel via putTravel when only travel changed', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    const putT = vi.spyOn(scheduleApi, 'putTravel').mockResolvedValue({})
    const putS = vi.spyOn(scheduleApi, 'putSchedule').mockResolvedValue(bundle)
    await mount()
    const addTravel = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Добавить поездку'))!
    await act(async () => addTravel.click())
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить')!
    await act(async () => save.click())
    expect(putT).toHaveBeenCalledTimes(1)
    expect(putS).not.toHaveBeenCalled()
  })

  it('clears the dirty markers after a successful save', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    vi.spyOn(scheduleApi, 'putSchedule').mockResolvedValue(bundle)
    await mount()
    const mon = container.querySelector('.weekday-row input[type="checkbox"]') as HTMLInputElement
    await act(async () => mon.click())
    expect(container.querySelector('.section.is-dirty')).not.toBeNull()
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить') as HTMLButtonElement
    await act(async () => save.click())
    await act(async () => {})
    expect(container.querySelector('.section.is-dirty')).toBeNull()
    expect(save.disabled).toBe(true)
  })

  it('blocks save and shows an inline error on an invalid edit', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    const put = vi.spyOn(scheduleApi, 'putSchedule').mockResolvedValue(bundle)
    await mount()
    // Set Пн start to 13:00 (after its 12:00 end) via the HourSelect.
    const startSelect = container.querySelector('.weekday-row .interval-row select') as HTMLSelectElement
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(startSelect, '13:00')
      startSelect.dispatchEvent(new Event('change', { bubbles: true }))
    })
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить')!
    await act(async () => save.click())
    expect(put).not.toHaveBeenCalled()
    expect(container.querySelector('.error-text')?.textContent).toContain('Пн')
  })
})
