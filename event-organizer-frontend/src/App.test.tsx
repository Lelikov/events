import { afterEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import App from './App.tsx'
import { AuthProvider } from './modules/auth/AuthContext.tsx'

let container: HTMLDivElement
let root: Root

function makeToken(payload: object): string {
  const base64 = btoa(JSON.stringify(payload)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `header.${base64}.signature`
}

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () =>
    root.render(
      <AuthProvider>
        <App />
      </AuthProvider>,
    ),
  )
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  sessionStorage.clear()
  window.history.replaceState(null, '', '/')
})

describe('App redirect', () => {
  it('redirects an unauthenticated visitor from a protected path to /login', async () => {
    window.history.replaceState(null, '', '/bookings')
    await mount()
    expect(window.location.pathname).toBe('/login')
    expect(container.querySelector('form')).not.toBeNull()
  })

  it('redirects an authenticated visitor away from /login to /', async () => {
    const token = makeToken({ sub: 'organizer@example.com', exp: Math.floor(Date.now() / 1000) + 3600 })
    sessionStorage.setItem('event_organizer_jwt', token)
    window.history.replaceState(null, '', '/login')
    await mount()
    expect(window.location.pathname).toBe('/')
    expect(container.querySelector('.card')?.textContent).toBe('Расписание')
  })
})
