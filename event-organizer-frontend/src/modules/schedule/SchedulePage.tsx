import { useEffect, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { TimeZoneField } from '../shared/TimeZoneField.tsx'
import { WeeklyHours } from './WeeklyHours.tsx'
import { DateOverrides } from './DateOverrides.tsx'
import { Travel } from './Travel.tsx'
import { getSchedule, putSchedule, putTravel } from './scheduleApi.ts'
import {
  bundleToState,
  buildTravel,
  buildUpsert,
  emptyDays,
  validate,
  type EditorState,
} from './schedule.ts'

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

export function SchedulePage() {
  const [state, setState] = useState<EditorState | null>(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState<string[]>([])
  const [travelError, setTravelError] = useState<string | null>(null)
  const [savedOk, setSavedOk] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    const defaultTz = browserTz()
    getSchedule()
      .then((bundle) => {
        if (cancelled) return
        setState(bundleToState(bundle, defaultTz))
      })
      .catch(() => {
        if (cancelled) return
        setState({ name: 'Моё расписание', timeZone: defaultTz, days: emptyDays(), overrides: [], travels: [] })
        setErrors(['Не удалось загрузить расписание'])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (loading || !state) {
    return <div className="card">Загрузка…</div>
  }

  async function handleSave() {
    if (!state) return
    setSavedOk(false)
    const validationErrors = validate(state)
    if (validationErrors.length > 0) {
      setErrors(validationErrors)
      return
    }
    setErrors([])
    setSaving(true)
    try {
      await putSchedule(buildUpsert(state))
      setSavedOk(true)
    } catch (err) {
      setErrors([upstreamMessage(err)])
    } finally {
      setSaving(false)
    }
  }

  async function handleSaveTravel() {
    if (!state) return
    setTravelError(null)
    setSaving(true)
    try {
      await putTravel(buildTravel(state))
    } catch (err) {
      setTravelError(upstreamMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="page-head">
        <h1>Расписание</h1>
        <button type="button" onClick={handleSave} disabled={saving}>
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
      {savedOk && <p className="ok-text">Сохранено</p>}

      <div className="section">
        <h2>Часовой пояс</h2>
        <TimeZoneField value={state.timeZone} onChange={(tz) => setState({ ...state, timeZone: tz })} />
      </div>

      <div className="section">
        <h2>Часы по неделям</h2>
        <WeeklyHours days={state.days} onChange={(days) => setState({ ...state, days })} />
      </div>

      <div className="section">
        <h2>Исключения по датам</h2>
        <DateOverrides overrides={state.overrides} onChange={(overrides) => setState({ ...state, overrides })} />
      </div>

      <div className="section">
        <div className="page-head">
          <h2>Поездки (временный часовой пояс)</h2>
          <button type="button" onClick={handleSaveTravel} disabled={saving}>
            Сохранить поездки
          </button>
        </div>
        {travelError && <p className="error-text">{travelError}</p>}
        <Travel travels={state.travels} onChange={(travels) => setState({ ...state, travels })} />
      </div>
    </div>
  )
}
