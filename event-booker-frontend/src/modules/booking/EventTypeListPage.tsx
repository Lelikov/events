import { useEffect, useState } from 'react'
import { listEventTypes } from './bookerApi.ts'
import { navigateTo } from '../shared/routing.ts'
import type { EventType } from './types.ts'

export function EventTypeListPage() {
  const [types, setTypes] = useState<EventType[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let active = true
    listEventTypes()
      .then((data) => active && setTypes(data))
      .catch(() => active && setError(true))
    return () => {
      active = false
    }
  }, [])

  if (error) {
    return (
      <main className="booker-shell">
        <p className="banner-error">Не удалось загрузить типы встреч. Обновите страницу.</p>
      </main>
    )
  }
  if (types === null) {
    return (
      <main className="booker-shell">
        <p className="muted">Загрузка…</p>
      </main>
    )
  }
  if (types.length === 0) {
    return (
      <main className="booker-shell">
        <h1>Запись на встречу</h1>
        <p className="muted">Сейчас нет доступных типов встреч.</p>
      </main>
    )
  }
  return (
    <main className="booker-shell">
      <h1>Выберите тип встречи</h1>
      {types.map((t) => (
        <button key={t.id} type="button" className="event-type-card" onClick={() => navigateTo(`/book/${t.id}`)}>
          <strong>{t.title}</strong>
          <div className="muted">{t.duration_minutes} мин</div>
        </button>
      ))}
    </main>
  )
}
