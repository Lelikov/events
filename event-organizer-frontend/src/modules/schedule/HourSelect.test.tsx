import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { HourSelect } from './HourSelect.tsx'
import { HOUR_OPTIONS } from './schedule.ts'

// Definite-assignment: every mounting test assigns these; the pure-constant
// test doesn't, so the teardown guards them at runtime.
let container!: HTMLDivElement
let root!: Root

async function mount(value: string, onChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<HourSelect value={value} onChange={onChange} ariaLabel="Начало" />))
  return onChange
}
afterEach(() => {
  if (root) act(() => root.unmount())
  container?.remove()
  vi.clearAllMocks()
})

function selectValue(el: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

describe('HourSelect', () => {
  it('exposes 24 whole-hour options from 00:00 to 23:00', () => {
    expect(HOUR_OPTIONS).toHaveLength(24)
    expect(HOUR_OPTIONS[0]).toBe('00:00')
    expect(HOUR_OPTIONS[9]).toBe('09:00')
    expect(HOUR_OPTIONS[23]).toBe('23:00')
  })

  it('renders a select of 24 whole-hour options for a whole-hour value', async () => {
    await mount('09:00')
    const select = container.querySelector('select') as HTMLSelectElement
    expect(select.value).toBe('09:00')
    expect(select.querySelectorAll('option')).toHaveLength(24)
  })

  it('preserves a legacy off-grid value as an extra selected option', async () => {
    await mount('09:30')
    const select = container.querySelector('select') as HTMLSelectElement
    expect(select.value).toBe('09:30')
    expect(select.querySelectorAll('option')).toHaveLength(25)
  })

  it('fires onChange with the picked whole-hour value', async () => {
    const onChange = await mount('09:00')
    const select = container.querySelector('select') as HTMLSelectElement
    await act(async () => selectValue(select, '14:00'))
    expect(onChange).toHaveBeenCalledWith('14:00')
  })
})
