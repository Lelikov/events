import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { Travel } from './Travel.tsx'
import type { TravelState } from './schedule.ts'

let container: HTMLDivElement
let root: Root

async function mount(travels: TravelState[], onChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<Travel travels={travels} onChange={onChange} />))
  return onChange
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('Travel', () => {
  it('adds a travel row', async () => {
    const onChange = await mount([])
    const addBtn = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Добавить поездку'))!
    await act(async () => addBtn.click())
    expect((onChange.mock.calls[0][0] as TravelState[])).toHaveLength(1)
  })

  it('renders existing rows and removes one', async () => {
    const onChange = await mount([{ start_date: '2026-08-01', end_date: '2026-08-10', time_zone: 'Asia/Dubai' }])
    expect(container.querySelectorAll('.travel-row')).toHaveLength(1)
    const del = container.querySelector('.travel-row .icon-button') as HTMLButtonElement
    await act(async () => del.click())
    expect((onChange.mock.calls[0][0] as TravelState[])).toHaveLength(0)
  })
})
