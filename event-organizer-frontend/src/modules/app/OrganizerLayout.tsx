import type { ReactNode } from 'react'
import { Icon, type IconName } from 'events-design-system'
import { useAuth } from '../auth/useAuth.ts'
import { decodeJwtPayload } from '../auth/jwt.ts'
import { confirmLeaveIfBlocked } from '../shared/navGuard.ts'
import { navigateTo } from '../shared/routing.ts'

type OrganizerLayoutProps = {
  pathname: string
  children: ReactNode
}

type NavItem = {
  label: string
  path: string
  icon: IconName
  match: (pathname: string) => boolean
}

const NAV_ITEMS: NavItem[] = [
  {
    label: 'Расписание',
    path: '/',
    icon: 'bookings',
    match: (pathname) => pathname === '/' || pathname === '/schedule',
  },
  {
    label: 'Брони',
    path: '/bookings',
    icon: 'dashboard',
    match: (pathname) => pathname === '/bookings',
  },
  {
    label: 'Профиль',
    path: '/profile',
    icon: 'users',
    match: (pathname) => pathname === '/profile',
  },
]

function sidebarIdentity(jwtToken: string | null): { name: string; email: string | null; initials: string } {
  const sub = jwtToken ? decodeJwtPayload(jwtToken)?.sub ?? null : null
  const email = sub && sub.includes('@') ? sub : null
  const name = email ? email.split('@')[0] : sub ?? 'Организатор'
  const initials = name.slice(0, 2).toUpperCase()
  return { name, email, initials }
}

export function OrganizerLayout({ pathname, children }: OrganizerLayoutProps) {
  const { logout, jwtToken } = useAuth()
  const identity = sidebarIdentity(jwtToken)

  function handleLogout() {
    // Check the unsaved-changes guard before tearing down the session, then skip
    // the guard on the redirect (the session is already gone).
    if (!confirmLeaveIfBlocked()) return
    logout()
    navigateTo('/login', { replace: true, skipGuard: true })
  }

  return (
    <div className="admin-shell org-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <div className="app-logo">EO</div>
          <div>
            <div className="app-brand-name">Кабинет организатора</div>
          </div>
        </div>

        <nav className="app-nav">
          {NAV_ITEMS.map((item) => {
            const active = item.match(pathname)
            return (
              <button
                key={item.path}
                type="button"
                className={`app-nav-item${active ? ' is-active' : ''}`}
                aria-current={active ? 'page' : undefined}
                onClick={() => navigateTo(item.path)}
              >
                <span className="app-nav-icon">
                  <Icon name={item.icon} />
                </span>
                <span>{item.label}</span>
              </button>
            )
          })}
        </nav>

        <div className="app-user">
          <div className="app-user-avatar">{identity.initials}</div>
          <div className="app-user-meta">
            <div className="app-user-name">{identity.name}</div>
            {identity.email && <div className="app-user-email">{identity.email}</div>}
          </div>
          <button type="button" className="app-logout" title="Выйти" aria-label="Выйти" onClick={handleLogout}>
            <Icon name="logout" size={15} />
          </button>
        </div>
      </aside>

      <main className="content org-content">{children}</main>
    </div>
  )
}
