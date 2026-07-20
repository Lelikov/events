import { useEffect, useSyncExternalStore } from 'react'
import { cancelLeave, confirmLeave, isLeavePending, subscribeGuard } from '../shared/navGuard.ts'

// Always-mounted (in OrganizerLayout) confirmation dialog for leaving a screen
// with unsaved changes. Driven by navGuard's pending state; replaces the native
// window.confirm for in-app navigation and logout.
export function LeaveGuardModal() {
  const open = useSyncExternalStore(subscribeGuard, isLeavePending, isLeavePending)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancelLeave()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (!open) return null

  return (
    <div className="modal-overlay" onMouseDown={cancelLeave}>
      <div
        className="modal-content leave-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="leave-modal-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id="leave-modal-title">Несохранённые изменения</h2>
        </div>
        <p className="leave-modal-text">Если уйти со страницы, несохранённые изменения будут потеряны.</p>
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={cancelLeave} autoFocus>
            Остаться
          </button>
          <button type="button" className="danger" onClick={confirmLeave}>
            Уйти без сохранения
          </button>
        </div>
      </div>
    </div>
  )
}
