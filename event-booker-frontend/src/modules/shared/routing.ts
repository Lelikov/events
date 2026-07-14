export type AppRoute =
  | { name: 'event-types' }
  | { name: 'book'; eventTypeId: string }
  | { name: 'not-found' }

export function parseRoute(pathname: string): AppRoute {
  if (pathname === '/' || pathname === '/event-types') {
    return { name: 'event-types' }
  }
  const bookMatch = pathname.match(/^\/book\/([^/]+)$/)
  if (bookMatch) {
    return { name: 'book', eventTypeId: decodeURIComponent(bookMatch[1]) }
  }
  return { name: 'not-found' }
}

export function navigateTo(path: string, options?: { replace?: boolean }): void {
  const method = options?.replace ? 'replaceState' : 'pushState'
  window.history[method](null, '', path)
  window.dispatchEvent(new Event('app:navigate'))
}
