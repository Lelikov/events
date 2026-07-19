import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ProfilePage } from './ProfilePage.tsx'
import * as profileApi from './profileApi.ts'
import { ApiError } from '../shared/api.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  vi.spyOn(profileApi, 'getProfile').mockResolvedValue({ name: 'Ада', email: 'ada@x.io', time_zone: 'Europe/Moscow' })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<ProfilePage />))
  await act(async () => {})
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

function setInput(sel: string, value: string) {
  const el = container.querySelector(sel) as HTMLInputElement
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('ProfilePage', () => {
  it('renders email read-only and saves name + tz only', async () => {
    const put = vi.spyOn(profileApi, 'updateProfile').mockResolvedValue({ name: 'Ада Л', email: 'ada@x.io', time_zone: 'Europe/Moscow' })
    await mount()
    expect((container.querySelector('input[name="email"]') as HTMLInputElement).readOnly).toBe(true)
    setInput('input[name="name"]', 'Ада Л')
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить профиль')!
    await act(async () => save.click())
    expect(put).toHaveBeenCalledWith({ name: 'Ада Л', time_zone: 'Europe/Moscow' })
  })

  it('blocks the password save when confirm mismatches', async () => {
    const chg = vi.spyOn(profileApi, 'changePassword').mockResolvedValue()
    await mount()
    setInput('input[name="old_password"]', 'old')
    setInput('input[name="new_password"]', 'newpass')
    setInput('input[name="confirm"]', 'other')
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сменить пароль')!
    await act(async () => save.click())
    expect(chg).not.toHaveBeenCalled()
    expect(container.textContent).toContain('не совпадают')
  })

  it('shows the 401 message on a wrong current password', async () => {
    vi.spyOn(profileApi, 'changePassword').mockRejectedValue(new ApiError('bad', 401, null))
    await mount()
    setInput('input[name="old_password"]', 'wrong')
    setInput('input[name="new_password"]', 'newpass')
    setInput('input[name="confirm"]', 'newpass')
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сменить пароль')!
    await act(async () => save.click())
    expect(container.textContent).toContain('Неверный текущий пароль')
  })
})
