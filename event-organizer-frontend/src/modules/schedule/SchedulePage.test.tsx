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

  it('saves with the exact upsert body incl. name', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    const put = vi.spyOn(scheduleApi, 'putSchedule').mockResolvedValue(bundle)
    await mount()
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить')!
    await act(async () => save.click())
    expect(put).toHaveBeenCalledWith({
      name: 'Моё',
      time_zone: 'Europe/Moscow',
      weekly_hours: [{ day_of_week: 1, start_time: '09:00', end_time: '12:00' }],
      date_overrides: [],
    })
  })

  it('travel save hits putTravel with the envelope', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    const putT = vi.spyOn(scheduleApi, 'putTravel').mockResolvedValue({})
    await mount()
    const saveT = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить поездки')!
    await act(async () => saveT.click())
    expect(putT).toHaveBeenCalledWith({ travel_schedules: [] })
  })

  it('blocks save and shows an inline error on invalid state', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue({
      ...bundle,
      weekly_hours: [{ day_of_week: 1, start_time: '12:00:00', end_time: '09:00:00' }],
    })
    const put = vi.spyOn(scheduleApi, 'putSchedule').mockResolvedValue(bundle)
    await mount()
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить')!
    await act(async () => save.click())
    expect(put).not.toHaveBeenCalled()
    expect(container.querySelector('.error-text')?.textContent).toContain('Пн')
  })
})
