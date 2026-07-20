import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { OrganizerLayout } from './OrganizerLayout.tsx'
import { AuthProvider } from '../auth/AuthContext.tsx'
import { cancelLeave, isLeavePending, setNavBlocker } from '../shared/navGuard.ts'

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

beforeEach(() => sessionStorage.clear())
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  setNavBlocker(null)
  if (isLeavePending()) cancelLeave()
})

describe('OrganizerLayout', () => {
  it('renders the three nav items and children', async () => {
    await mount('/')
    const labels = [...container.querySelectorAll('.app-nav-item')].map((b) => b.textContent)
    expect(labels).toEqual(['Брони', 'Расписание', 'Профиль'])
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

  it('shows the leave modal instead of logging out when there are unsaved changes', async () => {
    window.history.replaceState(null, '', '/schedule')
    setNavBlocker(() => true)
    sessionStorage.setItem('event_organizer_jwt', 'tok')
    await mount('/')
    await act(async () => (container.querySelector('.app-logout') as HTMLButtonElement).click())
    expect(container.querySelector('.modal-overlay')).not.toBeNull()
    expect(sessionStorage.getItem('event_organizer_jwt')).toBe('tok')
    expect(window.location.pathname).toBe('/schedule')
  })

  it('stays logged in when the leave modal is dismissed with Остаться', async () => {
    window.history.replaceState(null, '', '/schedule')
    setNavBlocker(() => true)
    sessionStorage.setItem('event_organizer_jwt', 'tok')
    await mount('/')
    await act(async () => (container.querySelector('.app-logout') as HTMLButtonElement).click())
    const stay = [...container.querySelectorAll('.modal-actions button')].find((b) => b.textContent === 'Остаться')!
    await act(async () => (stay as HTMLButtonElement).click())
    expect(container.querySelector('.modal-overlay')).toBeNull()
    expect(sessionStorage.getItem('event_organizer_jwt')).toBe('tok')
  })

  it('logs out when the leave modal is confirmed with Уйти', async () => {
    setNavBlocker(() => true)
    sessionStorage.setItem('event_organizer_jwt', 'tok')
    await mount('/')
    await act(async () => (container.querySelector('.app-logout') as HTMLButtonElement).click())
    const leave = [...container.querySelectorAll('.modal-actions button')].find((b) => b.textContent?.includes('Уйти'))!
    await act(async () => (leave as HTMLButtonElement).click())
    expect(sessionStorage.getItem('event_organizer_jwt')).toBeNull()
    expect(window.location.pathname).toBe('/login')
  })
})
