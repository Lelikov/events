// Guards navigation away from a screen with unsaved changes. A screen registers
// a blocker (its dirty predicate). When navigation is attempted, requestLeave
// runs the action immediately if nothing is at stake, otherwise it defers the
// action and marks a pending leave so the always-mounted LeaveGuardModal can ask
// the user to confirm. confirmLeave/cancelLeave are the modal's callbacks.
let blocker: (() => boolean) | null = null
let pending: (() => void) | null = null
const listeners = new Set<() => void>()

function emit(): void {
  listeners.forEach((l) => l())
}

export function setNavBlocker(fn: (() => boolean) | null): void {
  blocker = fn
}

// Subscribe to pending-state changes (used by the modal via useSyncExternalStore).
export function subscribeGuard(cb: () => void): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function isLeavePending(): boolean {
  return pending !== null
}

// Run `proceed` now if there is nothing to lose; otherwise stash it and open the
// confirmation modal.
export function requestLeave(proceed: () => void): void {
  if (!blocker || !blocker()) {
    proceed()
    return
  }
  pending = proceed
  emit()
}

// "Уйти" — close the modal, then run the deferred navigation.
export function confirmLeave(): void {
  const proceed = pending
  pending = null
  emit()
  proceed?.()
}

// "Остаться" / Escape / backdrop — drop the deferred navigation.
export function cancelLeave(): void {
  if (!pending) return
  pending = null
  emit()
}
