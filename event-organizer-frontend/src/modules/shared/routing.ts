import { requestLeave } from './navGuard.ts'

export type AppRoute =
  | { name: 'login' }
  | { name: 'schedule' }
  | { name: 'bookings' }
  | { name: 'profile' }
  | { name: 'not-found' }

export function parseRoute(pathname: string): AppRoute {
  if (pathname === '/login') {
    return { name: 'login' }
  }
  if (pathname === '/' || pathname === '/schedule') {
    return { name: 'schedule' }
  }
  if (pathname === '/bookings') {
    return { name: 'bookings' }
  }
  if (pathname === '/profile') {
    return { name: 'profile' }
  }
  return { name: 'not-found' }
}

export function navigateTo(path: string, options?: { replace?: boolean; skipGuard?: boolean }): void {
  const doNav = () => {
    const method = options?.replace ? 'replaceState' : 'pushState'
    window.history[method](null, '', path)
    window.dispatchEvent(new Event('app:navigate'))
  }
  const leavingCurrent = path !== window.location.pathname
  if (leavingCurrent && !options?.skipGuard) {
    // Deferred: runs now if clean, otherwise waits for the leave modal's confirm.
    requestLeave(doNav)
    return
  }
  doNav()
}
