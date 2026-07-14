import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { GuestForm } from './GuestForm.tsx'

let container: HTMLDivElement
let root: Root

async function mount(onSubmit = vi.fn(), onBack = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<GuestForm onSubmit={onSubmit} onBack={onBack} />)
  })
  return { onSubmit, onBack }
}

function setInput(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('GuestForm', () => {
  it('rejects an invalid email and does not call onSubmit', async () => {
    const { onSubmit } = await mount()
    const [name, email] = Array.from(container.querySelectorAll('input')) as HTMLInputElement[]
    await act(async () => {
      setInput(name, 'Анна')
      setInput(email, 'not-an-email')
    })
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Введите корректный email')
  })

  it('submits valid name + email', async () => {
    const { onSubmit } = await mount()
    const [name, email] = Array.from(container.querySelectorAll('input')) as HTMLInputElement[]
    await act(async () => {
      setInput(name, 'Анна')
      setInput(email, 'anna@example.com')
    })
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).toHaveBeenCalledWith('Анна', 'anna@example.com')
  })
})
