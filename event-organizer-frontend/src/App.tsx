import { useEffect, useMemo, useState } from 'react'
import { OrganizerLayout } from './modules/app/OrganizerLayout.tsx'
import { LoginPage } from './modules/auth/LoginPage.tsx'
import { useAuth } from './modules/auth/useAuth.ts'
import { SchedulePage } from './modules/schedule/SchedulePage.tsx'
import { BookingsPage } from './modules/bookings/BookingsPage.tsx'
import { ProfilePage } from './modules/profile/ProfilePage.tsx'
import { navigateTo, parseRoute } from './modules/shared/routing.ts'

function App() {
  const { isAuthenticated } = useAuth()
  const [pathname, setPathname] = useState(window.location.pathname)

  useEffect(() => {
    const syncPath = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', syncPath)
    window.addEventListener('app:navigate', syncPath)
    return () => {
      window.removeEventListener('popstate', syncPath)
      window.removeEventListener('app:navigate', syncPath)
    }
  }, [])

  const route = useMemo(() => parseRoute(pathname), [pathname])

  useEffect(() => {
    if (!isAuthenticated && route.name !== 'login') {
      navigateTo('/login', { replace: true })
      return
    }
    if (isAuthenticated && route.name === 'login') {
      navigateTo('/', { replace: true })
    }
  }, [isAuthenticated, route.name])

  if (route.name === 'login') {
    return <LoginPage />
  }

  return (
    <OrganizerLayout pathname={pathname}>
      {route.name === 'schedule' && <SchedulePage />}
      {route.name === 'bookings' && <BookingsPage />}
      {route.name === 'profile' && <ProfilePage />}
      {route.name === 'not-found' && (
        <div className="card">
          <h2>Страница не найдена</h2>
          <p>
            Адрес <code>{pathname}</code> не существует.
          </p>
          <button type="button" onClick={() => navigateTo('/', { replace: true })}>
            Вернуться к расписанию
          </button>
        </div>
      )}
    </OrganizerLayout>
  )
}

export default App
