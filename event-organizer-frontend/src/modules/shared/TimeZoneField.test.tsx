import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { TimeZoneField } from './TimeZoneField.tsx'

let container: HTMLDivElement
let root: Root

async function mount(node: React.ReactNode) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(node))
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('TimeZoneField', () => {
  it('renders read-only text when no onChange is given', async () => {
    await mount(<TimeZoneField value="Europe/Moscow" />)
    expect(container.querySelector('.tz-readonly')?.textContent).toBe('Москва')
  })

  it('opens the portaled dropdown on focus and selects an option', async () => {
    const onChange = vi.fn()
    await mount(<TimeZoneField value="Europe/Moscow" onChange={onChange} />)
    const input = container.querySelector('.tz-picker-input') as HTMLInputElement
    await act(async () => input.focus())
    const option = document.body.querySelector('.tz-option') as HTMLLIElement
    expect(option).toBeTruthy()
    await act(async () => option.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })))
    expect(onChange).toHaveBeenCalled()
  })
})
