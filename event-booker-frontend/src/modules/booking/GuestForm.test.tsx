import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { GuestForm } from './GuestForm.tsx'
import type { BookingField } from './types.ts'

let container: HTMLDivElement
let root: Root

const field = (o: Partial<BookingField>): BookingField => ({
  field_key: 'k',
  field_type: 'text',
  label: 'L',
  placeholder: null,
  required: false,
  options: [],
  ...o,
})

async function mount(fields: BookingField[], onSubmit = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<GuestForm fields={fields} onSubmit={onSubmit} onBack={vi.fn()} />))
  return { onSubmit }
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

function setInput(sel: string, value: string) {
  const el = container.querySelector(sel) as HTMLInputElement | HTMLTextAreaElement
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('GuestForm dynamic fields', () => {
  it('renders a control per field type and blocks submit on a missing required field', async () => {
    const fields = [
      field({ field_key: 'reason', field_type: 'textarea', label: 'Причина', required: true }),
      field({ field_key: 'topic', field_type: 'select', label: 'Тема', options: [{ value: 'a', label: 'A' }] }),
      field({ field_key: 'agree', field_type: 'boolean', label: 'Согласие' }),
    ]
    const { onSubmit } = await mount(fields)
    expect(container.querySelector('textarea')).toBeTruthy()
    expect(container.querySelector('select')).toBeTruthy()
    // fill name+email but not the required 'reason' → submit blocked
    setInput('input[name="name"]', 'Ada')
    setInput('input[name="email"]', 'ada@x.io')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).not.toHaveBeenCalled()
    // discriminating: the field-error message actually rendered (not just the field's own label)
    expect(container.querySelector('.field-error')?.textContent).toBe('Заполните поле «Причина»')
  })

  it('blocks submit on an invalid email', async () => {
    const { onSubmit } = await mount([])
    setInput('input[name="name"]', 'Ada')
    setInput('input[name="email"]', 'not-an-email')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).not.toHaveBeenCalled()
    expect(container.querySelector('.field-error')?.textContent).toContain('email')
  })

  it('submits name, email and answers when valid', async () => {
    const fields = [field({ field_key: 'reason', field_type: 'text', label: 'Причина', required: true })]
    const { onSubmit } = await mount(fields)
    setInput('input[name="name"]', 'Ada')
    setInput('input[name="email"]', 'ada@x.io')
    setInput('input[name="field-reason"]', 'help')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).toHaveBeenCalledWith('Ada', 'ada@x.io', [{ key: 'reason', value: 'help' }])
  })

  it('collects checkbox (multi) and boolean answers', async () => {
    const fields = [
      field({ field_key: 'topics', field_type: 'checkbox', label: 'Темы',
              options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }] }),
      field({ field_key: 'agree', field_type: 'boolean', label: 'Согласие' }),
    ]
    const { onSubmit } = await mount(fields)
    setInput('input[name="name"]', 'Ada')
    setInput('input[name="email"]', 'ada@x.io')
    const boxes = container.querySelectorAll('input[type="checkbox"]') // [topics-a, topics-b, agree]
    await act(async () => (boxes[0] as HTMLInputElement).click()) // check topic 'a'
    await act(async () => (boxes[1] as HTMLInputElement).click()) // check topic 'b'
    await act(async () => (boxes[1] as HTMLInputElement).click()) // uncheck 'b' → membership removed
    await act(async () => (boxes[2] as HTMLInputElement).click()) // agree = true
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).toHaveBeenCalledWith('Ada', 'ada@x.io', [
      { key: 'topics', value: ['a'] },
      { key: 'agree', value: true },
    ])
  })
})
