import { useEffect, useMemo, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { setNavBlocker } from '../shared/navGuard.ts'
import { TimeZoneField } from '../shared/TimeZoneField.tsx'
import { WeeklyHours } from './WeeklyHours.tsx'
import { DateOverrides } from './DateOverrides.tsx'
import { Travel } from './Travel.tsx'
import { getSchedule, putSchedule, putTravel } from './scheduleApi.ts'
import { bundleToState, buildTravel, buildUpsert, computeDirty, emptyDays, validate, type EditorState } from './schedule.ts'

function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

function upstreamMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 502) return 'Сервис временно недоступен. Попробуйте ещё раз.'
  if (err instanceof ApiError) return err.message
  return 'Не удалось сохранить. Попробуйте ещё раз.'
}

function DirtyBadge({ show }: { show: boolean }) {
  if (!show) return null
  return <span className="dirty-badge">не сохранено</span>
}

export function SchedulePage() {
  const [state, setState] = useState<EditorState | null>(null)
  const [saved, setSaved] = useState<EditorState | null>(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState<string[]>([])
  const [savedOk, setSavedOk] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    const defaultTz = browserTz()
    getSchedule()
      .then((bundle) => {
        if (cancelled) return
        const next = bundleToState(bundle, defaultTz)
        setState(next)
        setSaved(next)
      })
      .catch(() => {
        if (cancelled) return
        const fallback = { name: 'Моё расписание', timeZone: defaultTz, days: emptyDays(), overrides: [], travels: [] }
        setState(fallback)
        setSaved(fallback)
        setErrors(['Не удалось загрузить расписание'])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const dirty = useMemo(() => (state && saved ? computeDirty(state, saved) : null), [state, saved])
  const anyDirty = Boolean(dirty?.any)

  useEffect(() => {
    setNavBlocker(() => anyDirty)
    return () => setNavBlocker(null)
  }, [anyDirty])

  useEffect(() => {
    if (!anyDirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [anyDirty])

  if (loading || !state || !saved || !dirty) {
    return <div className="card">Загрузка…</div>
  }

  function edit(next: EditorState) {
    setSavedOk(false)
    setState(next)
  }

  async function handleSave() {
    if (!state || !dirty) return
    setSavedOk(false)
    const validationErrors = validate(state)
    if (validationErrors.length > 0) {
      setErrors(validationErrors)
      return
    }
    setErrors([])
    setSaving(true)
    try {
      if (dirty.schedule) await putSchedule(buildUpsert(state))
      if (dirty.travel) await putTravel(buildTravel(state))
      setSaved(state)
      setSavedOk(true)
    } catch (err) {
      setErrors([upstreamMessage(err)])
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="page-head">
        <h1>Расписание</h1>
        <button type="button" onClick={handleSave} disabled={saving || !dirty.any}>
          Сохранить
        </button>
      </div>

      {errors.length > 0 && (
        <div className="section">
          {errors.map((e) => (
            <p className="error-text" key={e}>
              {e}
            </p>
          ))}
        </div>
      )}
      {savedOk && !dirty.any && <p className="ok-text">Сохранено</p>}

      <div className={`section${dirty.tz ? ' is-dirty' : ''}`}>
        <div className="section-head">
          <h2>Часовой пояс</h2>
          <DirtyBadge show={dirty.tz} />
        </div>
        <TimeZoneField value={state.timeZone} onChange={(tz) => edit({ ...state, timeZone: tz })} />
      </div>

      <div className={`section${dirty.weekly ? ' is-dirty' : ''}`}>
        <div className="section-head">
          <h2>Часы по неделям</h2>
          <DirtyBadge show={dirty.weekly} />
        </div>
        <WeeklyHours days={state.days} onChange={(days) => edit({ ...state, days })} />
      </div>

      <div className={`section${dirty.overrides ? ' is-dirty' : ''}`}>
        <div className="section-head">
          <h2>Исключения по датам</h2>
          <DirtyBadge show={dirty.overrides} />
        </div>
        <DateOverrides overrides={state.overrides} onChange={(overrides) => edit({ ...state, overrides })} />
      </div>

      <div className={`section${dirty.travel ? ' is-dirty' : ''}`}>
        <div className="section-head">
          <h2>Поездки (временный часовой пояс)</h2>
          <DirtyBadge show={dirty.travel} />
        </div>
        <Travel travels={state.travels} onChange={(travels) => edit({ ...state, travels })} />
      </div>
    </div>
  )
}
