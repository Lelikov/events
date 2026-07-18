import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { listTimeZones, timeZoneLabel } from './timezones.ts'

// Time-zone picker. Editable (slot step): a custom search-as-you-type combobox
// with its own dropdown (portaled to <body> so it is never clipped by the
// card's overflow). Read-only (details step): plain muted text, visibly not a
// control.
type Props = {
  value: string
  onChange?: (id: string) => void
}

export function TimeZoneField({ value, onChange }: Props) {
  if (!onChange) return <TimeZoneReadonly value={value} />
  return <TimeZoneCombo value={value} onChange={onChange} />
}

function TimeZoneReadonly({ value }: { value: string }) {
  const label = useMemo(() => timeZoneLabel(value), [value])
  return <div className="tz-readonly">{label}</div>
}

function TimeZoneCombo({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const zones = useMemo(() => listTimeZones(), [])
  const currentLabel = useMemo(() => timeZoneLabel(value), [value])
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [rect, setRect] = useState<{ left: number; top: number; width: number } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return zones
    return zones.filter((z) => z.label.toLowerCase().includes(q))
  }, [zones, query])

  function place() {
    const el = inputRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setRect({ left: r.left, top: r.bottom + 4, width: Math.max(r.width, 240) })
  }

  function openList() {
    place()
    setQuery('')
    const idx = zones.findIndex((z) => z.id === value)
    setActive(idx < 0 ? 0 : idx)
    setOpen(true)
  }

  function choose(id: string) {
    onChange(id)
    setOpen(false)
    setQuery('')
    inputRef.current?.blur()
  }

  // Close on outside click / scroll / resize (positioning is fixed to the input).
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (inputRef.current?.contains(t) || listRef.current?.contains(t)) return
      setOpen(false)
    }
    // Close on page/ancestor scroll (the dropdown is position:fixed and would
    // detach from the input) — but NOT when scrolling inside the dropdown itself.
    const onScroll = (e: Event) => {
      if (listRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    const onResize = () => setOpen(false)
    document.addEventListener('mousedown', onDown)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [open])

  // Keep the highlighted option centred in view (on open it is the current zone).
  useEffect(() => {
    if (!open) return
    const ul = listRef.current
    const el = ul?.querySelector<HTMLElement>('.tz-option.is-active')
    if (ul && el) ul.scrollTop = el.offsetTop - ul.clientHeight / 2 + el.clientHeight / 2
  }, [active, open])

  function onKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) {
        openList()
        return
      }
      setActive((i) => Math.min(i + 1, filtered.length - 1))
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      const opt = filtered[active]
      if (open && opt) choose(opt.id)
      return
    }
    if (e.key === 'Escape') setOpen(false)
  }

  return (
    <div className="tz-field">
      <input
        ref={inputRef}
        className="tz-picker-input"
        role="combobox"
        aria-expanded={open}
        aria-label="Часовой пояс"
        placeholder="Начните вводить город…"
        value={open ? query : currentLabel}
        onFocus={openList}
        onChange={(e) => {
          setQuery(e.target.value)
          setActive(0)
          place()
          setOpen(true)
        }}
        onKeyDown={onKeyDown}
      />
      {open &&
        rect &&
        createPortal(
          <ul
            ref={listRef}
            className="tz-dropdown"
            role="listbox"
            style={{ position: 'fixed', left: rect.left, top: rect.top, width: rect.width, zIndex: 60 }}
          >
            {filtered.length === 0 && <li className="tz-option-empty">Ничего не найдено</li>}
            {filtered.map((z, i) => (
              <li
                key={z.id}
                role="option"
                aria-selected={z.id === value}
                className={`tz-option${i === active ? ' is-active' : ''}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(z.id)
                }}
              >
                {z.label}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  )
}
