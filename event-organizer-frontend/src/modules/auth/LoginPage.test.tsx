import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { LoginPage } from './LoginPage.tsx'
import { AuthProvider } from './AuthContext.tsx'
import { ApiError } from '../shared/api.ts'
import * as authApi from './authApi.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () =>
    root.render(
      <AuthProvider>
        <LoginPage />
      </AuthProvider>,
    ),
  )
}

beforeEach(() => {
  sessionStorage.clear()
})

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

describe('LoginPage', () => {
  it('logs in and stores the token', async () => {
    const spy = vi.spyOn(authApi, 'login').mockResolvedValue({ access_token: 'tok.123' })
    await mount()
    setInput('input[name="email"]', 'organizer@example.com')
    setInput('input[name="password"]', 'secret')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(spy).toHaveBeenCalledWith({ email: 'organizer@example.com', password: 'secret' })
    expect(sessionStorage.getItem('event_organizer_jwt')).toBe('tok.123')
  })

  it('shows the Russian message on 401', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new ApiError('bad', 401, null))
    await mount()
    setInput('input[name="email"]', 'x@y.z')
    setInput('input[name="password"]', 'wrong')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(container.querySelector('.error-text')?.textContent).toBe('Неверный email или пароль')
    expect(sessionStorage.getItem('event_organizer_jwt')).toBeNull()
  })
})
