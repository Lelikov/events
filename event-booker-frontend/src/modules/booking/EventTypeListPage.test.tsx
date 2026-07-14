import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { EventTypeListPage } from './EventTypeListPage.tsx'

vi.mock('./bookerApi.ts', () => ({ listEventTypes: vi.fn() }))
vi.mock('../shared/routing.ts', () => ({ navigateTo: vi.fn() }))

import { listEventTypes } from './bookerApi.ts'
import { navigateTo } from '../shared/routing.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<EventTypeListPage />)
  })
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('EventTypeListPage', () => {
  it('renders event-type cards and navigates on click', async () => {
    vi.mocked(listEventTypes).mockResolvedValue([
      { id: 'e1', slug: 'intro', title: 'Знакомство', duration_minutes: 30 },
    ])
    await mount()
    await act(async () => {})
    const card = container.querySelector('.event-type-card') as HTMLButtonElement
    expect(card.textContent).toContain('Знакомство')
    expect(card.textContent).toContain('30')
    await act(async () => card.click())
    expect(vi.mocked(navigateTo)).toHaveBeenCalledWith('/book/e1')
  })

  it('shows an error message when the fetch fails', async () => {
    vi.mocked(listEventTypes).mockRejectedValue(new Error('boom'))
    await mount()
    await act(async () => {})
    expect(container.textContent).toContain('Не удалось загрузить')
  })
})
