import { useEffect, useMemo, useState } from 'react'
import { parseRoute } from './modules/shared/routing.ts'
import { EventTypeListPage } from './modules/booking/EventTypeListPage.tsx'
import './App.css'

function BookPlaceholder({ eventTypeId }: { eventTypeId: string }) {
  return <h1>Бронирование: {eventTypeId}</h1>
}

function NotFound() {
  return (
    <main>
      <h1>Страница не найдена</h1>
      <a href="/">На главную</a>
    </main>
  )
}

export default function App() {
  const [pathname, setPathname] = useState(window.location.pathname)
  useEffect(() => {
    const sync = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', sync)
    window.addEventListener('app:navigate', sync)
    return () => {
      window.removeEventListener('popstate', sync)
      window.removeEventListener('app:navigate', sync)
    }
  }, [])
  const route = useMemo(() => parseRoute(pathname), [pathname])
  if (route.name === 'event-types') return <EventTypeListPage />
  if (route.name === 'book') return <BookPlaceholder eventTypeId={route.eventTypeId} />
  return <NotFound />
}
