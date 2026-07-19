import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { DateOverrides } from './DateOverrides.tsx'
import type { OverrideState } from './schedule.ts'

let container: HTMLDivElement
let root: Root

async function mount(overrides: OverrideState[], onChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<DateOverrides overrides={overrides} onChange={onChange} />))
  return onChange
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('DateOverrides', () => {
  it('adds a new override row', async () => {
    const onChange = await mount([])
    const addBtn = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Добавить дату'))!
    await act(async () => addBtn.click())
    expect((onChange.mock.calls[0][0] as OverrideState[])).toHaveLength(1)
  })

  it('toggling full-day clears the times', async () => {
    const onChange = await mount([{ date: '2026-07-25', fullDay: false, start: '10:00', end: '14:00' }])
    const box = container.querySelector('.override-row input[type="checkbox"]') as HTMLInputElement
    await act(async () => box.click())
    const next = (onChange.mock.calls[0][0] as OverrideState[])[0]
    expect(next.fullDay).toBe(true)
  })

  it('removes a row', async () => {
    const onChange = await mount([{ date: '2026-07-25', fullDay: true, start: '', end: '' }])
    const del = container.querySelector('.override-row .icon-button') as HTMLButtonElement
    await act(async () => del.click())
    expect((onChange.mock.calls[0][0] as OverrideState[])).toHaveLength(0)
  })
})
