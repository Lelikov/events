# Shortener HTML Error Pages (404/410) — Design

**Date:** 2026-06-15
**Status:** Approved (brainstorm)

## Problem

The public redirect `GET /{ident}` currently returns plain JSON (`{"detail": ...}`)
for `404` (unknown short link) and `410` (link out of its active window). These
links are browser-facing meeting links, so a human hitting a dead/early link sees
raw JSON. Serve clean HTML pages instead.

## Scope

Only the **public redirect** route `GET /{ident}` in `event-shortener`. Every
other endpoint — `/api/v1/*` (incl. `/api/v1/urls/{ident}/stats`), `/health`,
`/ready`, `/metrics` — keeps returning JSON, unchanged.

## Decisions (from brainstorm)

- **Distinguish the two 410 cases** (not-yet-active vs expired).
- **Minimalist, unbranded** pages (icon + heading + one line). No logo, no links,
  no support contact, no configurable settings.

## The three pages

Self-contained HTML each: `<!doctype html>`, `<html lang="ru">`, `<meta charset>`
+ viewport, inline `<style>` (no external assets), a centered card, system font
stack, responsive.

| Case | Status | Icon | Heading | Body line |
|---|---|---|---|---|
| Unknown ident | 404 | 🔗 | Ссылка не найдена | Проверьте адрес ссылки. |
| `now < not_before` | 410 | ⏳ | Ссылка ещё не активна | Она откроется незадолго до начала встречи. |
| `now >= expires_at` | 410 | ✅ | Встреча завершена | Эта ссылка больше не активна. |

## Components

### `event_shortener/pages.py` (new)
- `error_page(*, icon: str, title: str, message: str) -> str` — returns the full
  HTML document for an error card (the single template; inline CSS).
- Three module-level rendered strings built from it: `NOT_FOUND_PAGE`,
  `NOT_ACTIVE_PAGE`, `EXPIRED_PAGE`.

### `event_shortener/routes.py` — redirect handler
Replace the `raise HTTPException(...)` paths with `HTMLResponse(page,
status_code=...)`, and split the out-of-window branch by reason:

```
record = resolve(ident)
if record is None:                       → HTMLResponse(NOT_FOUND_PAGE, 404)
now = datetime.now(UTC)
if record.not_before and now < record.not_before:   → HTMLResponse(NOT_ACTIVE_PAGE, 410)
if record.expires_at and now >= record.expires_at:  → HTMLResponse(EXPIRED_PAGE, 410)
register_click(ident); → 307 to long_url   (unchanged)
```

- Every error response carries `Cache-Control: no-store` so a "not yet active"
  page is never cached and a later (in-window) click re-fetches and redirects.
- This replaces the current `_within_window` helper; the 307 path (and its click
  increment) is unchanged.
- Metrics: keep the existing `REDIRECTS_TOTAL` labels — `result="not_found"` for
  404, `result="expired"` for both 410 reasons, `result="ok"` for 307 (no new
  label values, observability untouched).

## Data flow

```
GET /{ident}
  unknown            → 404 text/html  (NOT_FOUND_PAGE)   + Cache-Control: no-store
  now < not_before   → 410 text/html  (NOT_ACTIVE_PAGE)  + Cache-Control: no-store
  now >= expires_at  → 410 text/html  (EXPIRED_PAGE)     + Cache-Control: no-store
  in window          → 307 redirect (unchanged) + click_count += 1
```

## Error handling

- The HTML responses ARE the error responses; no exceptions raised on the redirect
  path. API routes still raise `HTTPException` (JSON) — unaffected.

## Testing

- 404 unknown ident → status 404, `content-type: text/html`, body contains
  "Ссылка не найдена".
- `not_before` in the future → 410, HTML, body contains "ещё не активна".
- `expires_at` in the past → 410, HTML, body contains "Встреча завершена".
- in-window redirect → still 307 (existing test stays green).
- `GET /api/v1/urls/{ident}/stats` for an unknown ident → still **JSON** 404
  (assert `content-type: application/json`), proving the HTML is scoped to the
  redirect route only.

## Out of scope (YAGNI)

- Content negotiation (always HTML on the redirect route).
- Branding, logos, links, support contact, configurable text.
- Localization (Russian only).
- External/static assets.
