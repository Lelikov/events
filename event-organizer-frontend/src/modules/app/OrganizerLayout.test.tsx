import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { OrganizerLayout } from './OrganizerLayout.tsx'
import { AuthProvider } from '../auth/AuthContext.tsx'
import { setNavBlocker } from '../shared/navGuard.ts'

let container: HTMLDivElement
let root: Root

async function mount(pathname: string) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () =>
    root.render(
      <AuthProvider>
        <OrganizerLayout pathname={pathname}>
          <div className="probe">child</div>
        </OrganizerLayout>
      </AuthProvider>,
    ),
  )
}

const realConfirm = window.confirm
beforeEach(() => sessionStorage.clear())
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  setNavBlocker(null)
  window.confirm = realConfirm
})

describe('OrganizerLayout', () => {
  it('renders the three nav items and children', async () => {
    await mount('/')
    const labels = [...container.querySelectorAll('.app-nav-item')].map((b) => b.textContent)
    expect(labels).toEqual(['Расписание', 'Брони', 'Профиль'])
    expect(container.querySelector('.probe')?.textContent).toBe('child')
  })

  it('marks the active item by pathname', async () => {
    await mount('/bookings')
    const active = container.querySelector('.app-nav-item.is-active')
    expect(active?.textContent).toBe('Брони')
  })

  it('logout clears the session and navigates to /login', async () => {
    sessionStorage.setItem('event_organizer_jwt', 'tok')
    await mount('/')
    await act(async () => (container.querySelector('.app-logout') as HTMLButtonElement).click())
    expect(sessionStorage.getItem('event_organizer_jwt')).toBeNull()
    expect(window.location.pathname).toBe('/login')
  })

  it('does not log out when the unsaved-changes guard is declined', async () => {
    window.history.replaceState(null, '', '/schedule')
    setNavBlocker(() => true)
    window.confirm = vi.fn().mockReturnValue(false)
    sessionStorage.setItem('event_organizer_jwt', 'tok')
    await mount('/')
    await act(async () => (container.querySelector('.app-logout') as HTMLButtonElement).click())
    expect(sessionStorage.getItem('event_organizer_jwt')).toBe('tok')
    expect(window.location.pathname).toBe('/schedule')
  })

  it('logs out when the unsaved-changes guard is confirmed', async () => {
    setNavBlocker(() => true)
    window.confirm = vi.fn().mockReturnValue(true)
    sessionStorage.setItem('event_organizer_jwt', 'tok')
    await mount('/')
    await act(async () => (container.querySelector('.app-logout') as HTMLButtonElement).click())
    expect(sessionStorage.getItem('event_organizer_jwt')).toBeNull()
    expect(window.location.pathname).toBe('/login')
  })
})
