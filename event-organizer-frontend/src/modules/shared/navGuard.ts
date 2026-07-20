// A single active navigation blocker, registered by whichever screen has
// unsaved changes. navigateTo (and the logout handler) consult it before
// leaving so the user can confirm losing edits.
let blocker: (() => boolean) | null = null

export function setNavBlocker(fn: (() => boolean) | null): void {
  blocker = fn
}

// true = navigation may proceed (nothing to lose, or the user confirmed leaving).
export function confirmLeaveIfBlocked(): boolean {
  if (blocker && blocker()) {
    return window.confirm('Есть несохранённые изменения. Уйти без сохранения?')
  }
  return true
}
