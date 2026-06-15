# Shortener Google-Meet Ident + Click Counter — Design

**Date:** 2026-06-15
**Status:** Approved (brainstorm)

## Problem

Two changes to the URL shortener plus a display change in the admin:
1. `event-shortener` idents should look like Google Meet codes — three groups of
   three lowercase letters, hyphen-separated (`xxx-xxx-xxx`) — instead of the
   current 7-char base62.
2. Each short URL needs a **click counter**, incremented on every redirect.
3. The admin booking-detail card must show that click count next to the meeting
   link.

The counter lives in `event-shortener`'s own DB, but the booking card is served
by `event-admin` reading `event-saver`'s DB — so `event-admin` must fetch the
count from the shortener.

## Decisions (from brainstorm)

- **Count delivery:** REST — `event-admin` derives the ident from the stored
  `meeting_url` and calls a new `event-shortener` stats endpoint. (Not eventing:
  the shortener has no RabbitMQ wiring and per-click events would be noisy.)
- **Click semantics:** every successful `307` redirect counts `+1`. `410`
  (out-of-window) and `404` (unknown) do not count. No unique-visitor dedup (YAGNI).
- **Increment timing:** synchronous in the redirect request (one cheap atomic
  `UPDATE`); accurate count over marginally higher redirect latency.

## Key facts (verified)

- `event-booking`'s `UrlShortenerAdapter` returns the meeting link as
  `f"{shortener_base_url}/{ident}"` — so the **ident is the last path segment**
  of the stored `meeting_url`.
- `event-shortener` redirect is `GET /{ident}` → `resolve` → window check →
  `307`/`410`/`404` (`event_shortener/routes.py`). `short_urls` has a unique
  `ident`; the controller regenerates on a unique-violation, so the generator
  only needs to be uniformly random.
- `event-admin` has no shortener client yet (only users/notifier/event-publisher).

## Components

### 1. event-shortener — ident format

`event_shortener/ident.py`: replace the base62 generator with the Meet format —
three groups of three `a-z` letters joined by `-` (e.g. `qmk-rba-htz`). Keyspace
26⁹ ≈ 5.4×10¹². DB uniqueness + controller regeneration unchanged. **Only new
idents** use this format; existing 7-char idents keep resolving (exact-match
lookup), no backfill.

### 2. event-shortener — click counter

- Alembic migration: `ALTER TABLE short_urls ADD COLUMN click_count BIGINT NOT
  NULL DEFAULT 0` (existing rows → 0).
- Redirect handler: after the in-window check, on the `307` path, atomically
  increment: `UPDATE short_urls SET click_count = click_count + 1 WHERE
  ident = :ident`. A failure here is logged but never blocks the redirect. `410`
  / `404` paths do not increment.
- New endpoint (api-key gated, same as the other `/api/v1/*`):
  `GET /api/v1/urls/{ident}/stats` → `{"ident": <str>, "click_count": <int>}`;
  `404` when the ident is unknown.

### 3. event-admin — fetch the count via REST

- New `ShortenerClient` (httpx adapter, `IShortenerClient` protocol):
  `get_click_count(ident) -> int | None`. Calls `GET
  /api/v1/urls/{ident}/stats` with `Authorization: Bearer <SHORTENER_API_KEY>`.
  Returns the count, or `None` on `404`/transport/5xx (best-effort).
- Settings + compose: `SHORTENER_URL` (default internal
  `http://event-shortener:8888`), `SHORTENER_API_KEY` (matches the shortener's,
  default the existing dev key). DI-registered app-scoped.
- Booking-detail assembly: for each `meeting_link`, derive the ident as the last
  non-empty path segment of `meeting_url`, fetch its count, attach `click_count`.
  The per-link stats calls run concurrently (httpx — does not touch the
  request-scoped `AsyncSession`, so the no-`asyncio.gather`-on-SqlExecutor rule
  is not violated).
- `BookingMeetingLinkItemDto` and `BookingMeetingLinkItemResponse` gain
  `click_count: int | None`.
- **Graceful degradation:** shortener unreachable, ident unknown, or a
  non-shortener `meeting_url` → `click_count = null`; the booking detail still
  returns `200`. The shortener is never on the booking-detail critical path.

### 4. event-admin-frontend

- `MeetingLink` type gains `click_count: number | null`.
- On the booking card, next to each meeting link, render `переходов: N`
  (or `—` when `click_count` is `null`).

## Data flow

```
Redirect:
  GET /{ident}  → resolve(ident)
                → in window?  yes → UPDATE click_count+1 ; 307 to long_url
                              no  → 410 (no increment)
                  unknown      → 404 (no increment)

Booking detail (admin):
  event-admin reads meeting_links from event-saver's DB
    for each link: ident = last path segment of meeting_url
                   ShortenerClient.get_click_count(ident)  (concurrent, best-effort)
    → meeting_links[].click_count  → response → frontend renders "переходов: N"
```

## Error handling

- Redirect increment failure → log, still `307` (user's click is never broken).
- Shortener `404` / transport / `5xx` for a stats lookup → `click_count = null`,
  booking detail `200`.
- `meeting_url` that isn't a shortener link (no resolvable ident) → stats `404`
  → `null`.

## Testing

- **shortener:** ident matches `^[a-z]{3}-[a-z]{3}-[a-z]{3}$`; redirect
  increments only on `307` (not `410`/`404`); `GET .../{ident}/stats` returns the
  count and `404` for unknown; migration adds `click_count` defaulting to 0.
- **event-admin:** `ShortenerClient.get_click_count` (200/404/error → int/None);
  ident extraction from a `meeting_url`; booking detail attaches `click_count`
  and degrades to `null` when the shortener errors; no-`gather`-on-session rule
  respected.
- **frontend:** renders `переходов: N` next to a link, and `—` when `null`.

## Out of scope (YAGNI)

- Unique-visitor counting / dedup, per-click history/analytics.
- Backfilling existing 7-char idents to the new format.
- Any RabbitMQ/eventing for clicks.
- Showing the counter anywhere other than the booking-detail card.
