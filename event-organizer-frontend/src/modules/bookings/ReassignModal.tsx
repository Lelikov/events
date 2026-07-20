import { useEffect, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { getReassignTargets, reassignBooking } from './bookingsApi.ts'
import type { ReassignTarget } from './types.ts'

type Props = { bookingId: string; onClose: () => void; onReassigned: () => void }

export function ReassignModal({ bookingId, onClose, onReassigned }: Props) {
  const [targets, setTargets] = useState<ReassignTarget[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    getReassignTargets(bookingId)
      .then((t) => {
        if (!cancelled) setTargets(t)
      })
      .catch(() => {
        if (!cancelled) setLoadError(true)
      })
    return () => {
      cancelled = true
    }
  }, [bookingId])

  async function confirm() {
    if (!picked) return
    setSaving(true)
    setSubmitError(null)
    try {
      await reassignBooking(bookingId, picked)
      onReassigned()
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Не удалось переназначить. Попробуйте ещё раз.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div
        className="modal-content leave-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reassign-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id="reassign-title">Переназначить бронь</h2>
        </div>
        <div className="target-list">
          {targets === null && !loadError && <span className="muted">Загрузка…</span>}
          {loadError && <span className="error-text">Не удалось загрузить список хостов</span>}
          {targets !== null && targets.length === 0 && (
            <span className="muted">Нет других хостов для этого типа встречи</span>
          )}
          {targets?.map((t) => (
            <button
              type="button"
              key={t.user_id}
              className={`target-row${picked === t.user_id ? ' is-selected' : ''}`}
              onClick={() => setPicked(t.user_id)}
            >
              <span className="target-name">{t.name ?? t.email}</span>
              {t.name && <span className="target-email">{t.email}</span>}
            </button>
          ))}
        </div>
        {submitError && <p className="error-text">{submitError}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Отмена
          </button>
          <button type="button" onClick={confirm} disabled={!picked || saving}>
            Переназначить
          </button>
        </div>
      </div>
    </div>
  )
}
