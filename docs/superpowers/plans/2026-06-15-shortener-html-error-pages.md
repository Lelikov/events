# Shortener HTML Error Pages (404/410) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The event-shortener public redirect serves minimalist HTML pages for 404 (unknown link) and the two 410 cases (not-yet-active / expired) instead of JSON; API endpoints stay JSON.

**Architecture:** A new `pages.py` holds one HTML template helper + three pre-rendered page strings. The `GET /{ident}` redirect handler returns `HTMLResponse(..., status_code=..., headers={"Cache-Control": "no-store"})` on the error paths, splitting the out-of-window branch into `not_before` vs `expires_at`; the 307 success path (and its click increment) is unchanged. Metrics labels are unchanged.

**Tech Stack:** Python 3.14, FastAPI (`HTMLResponse`), `uv`, pytest (real-Postgres harness via conftest).

**Spec:** `docs/superpowers/specs/2026-06-15-shortener-html-error-pages-design.md`

**Conventions:** No `elif`, avoid `else`. Ruff line length 120. `pre-commit` NOT installed → commit `--no-verify`. `event-shortener/` is its OWN git repo — git from `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener`. The redirect integration tests need the conftest's real/ephemeral Postgres (available in this environment); `tests/test_pages.py` is a pure unit test (no DB).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `event_shortener/pages.py` | Create | HTML template + `error_page()` + 3 page constants |
| `tests/test_pages.py` | Create | unit test for the page strings |
| `event_shortener/routes.py` | Modify | redirect handler returns HTML; drop `_within_window` |
| `tests/test_api.py` | Modify | redirect HTML 404/410 + stats-stays-JSON tests |

---

## Task 1: HTML page module

**Files:**
- Create: `event_shortener/pages.py`
- Create: `tests/test_pages.py`

- [ ] **Step 1: Write the failing unit test** — create `tests/test_pages.py`:

```python
from event_shortener.pages import EXPIRED_PAGE, NOT_ACTIVE_PAGE, NOT_FOUND_PAGE, error_page


def test_error_page_renders_fields() -> None:
    html = error_page(icon="🔗", title="Заголовок", message="Сообщение")
    assert html.startswith("<!doctype html>")
    assert 'lang="ru"' in html
    assert "🔗" in html
    assert "Заголовок" in html
    assert "Сообщение" in html


def test_prebuilt_pages_have_expected_text() -> None:
    assert "Ссылка не найдена" in NOT_FOUND_PAGE
    assert "ещё не активна" in NOT_ACTIVE_PAGE
    assert "Встреча завершена" in EXPIRED_PAGE
```

- [ ] **Step 2: Run it to confirm it FAILS**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener && uv run pytest tests/test_pages.py -v`
Expected: FAIL — `event_shortener.pages` does not exist (ImportError).

- [ ] **Step 3: Create `event_shortener/pages.py`** with exactly:

```python
"""Minimal, self-contained HTML pages for the public redirect's error states.

Browser-facing only: the redirect route returns these instead of JSON. The
content is static (no user input is interpolated), so str.format on the template
carries no injection risk.
"""

_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #f5f7fa; color: #1f2933; padding: 24px;
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .card {{
    background: #ffffff; border: 1px solid #e4e7eb; border-radius: 16px;
    padding: 40px 32px; max-width: 420px; width: 100%; text-align: center;
    box-shadow: 0 8px 30px rgba(15, 23, 42, 0.06);
  }}
  .icon {{ font-size: 56px; line-height: 1; margin-bottom: 16px; }}
  h1 {{ font-size: 22px; margin: 0 0 8px; }}
  p {{ font-size: 15px; line-height: 1.5; color: #616e7c; margin: 0; }}
</style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
  </div>
</body>
</html>
"""


def error_page(*, icon: str, title: str, message: str) -> str:
    """Render the error-card HTML document."""
    return _TEMPLATE.format(icon=icon, title=title, message=message)


NOT_FOUND_PAGE = error_page(
    icon="🔗",
    title="Ссылка не найдена",
    message="Проверьте адрес ссылки.",
)
NOT_ACTIVE_PAGE = error_page(
    icon="⏳",
    title="Ссылка ещё не активна",
    message="Она откроется незадолго до начала встречи.",
)
EXPIRED_PAGE = error_page(
    icon="✅",
    title="Встреча завершена",
    message="Эта ссылка больше не активна.",
)
```

- [ ] **Step 4: Run the test to confirm it PASSES + lint**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener && uv run pytest tests/test_pages.py -v && ruff check .`
Expected: 2 passed, no lint errors.

- [ ] **Step 5: Commit (event-shortener repo)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener
git add event_shortener/pages.py tests/test_pages.py
git commit --no-verify -m "feat(shortener): HTML error-page templates for redirect

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Redirect handler serves the HTML pages

**Files:**
- Modify: `event_shortener/routes.py`
- Test: `event_shortener/tests/test_api.py`

- [ ] **Step 1: Add failing integration tests** — append to `tests/test_api.py`:

```python
def test_unknown_ident_returns_html_404(client) -> None:
    resp = client.get("/no-such-ident", follow_redirects=False)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers.get("cache-control") == "no-store"
    assert "Ссылка не найдена" in resp.text


def test_not_yet_active_returns_html_410(client) -> None:
    now = time.time()
    client.post(
        SHORTEN_URL,
        json=_payload("ext-early", long_url="https://x.example", not_before=now + 3600, expires_at=now + 7200),
    )
    ident = client.get("/api/v1/urls/external/ext-early").json()["ident"]
    resp = client.get(f"/{ident}", follow_redirects=False)
    assert resp.status_code == 410
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers.get("cache-control") == "no-store"
    assert "ещё не активна" in resp.text


def test_expired_returns_html_410(client) -> None:
    now = time.time()
    client.post(
        SHORTEN_URL,
        json=_payload("ext-old", long_url="https://x.example", not_before=now - 7200, expires_at=now - 3600),
    )
    ident = client.get("/api/v1/urls/external/ext-old").json()["ident"]
    resp = client.get(f"/{ident}", follow_redirects=False)
    assert resp.status_code == 410
    assert resp.headers["content-type"].startswith("text/html")
    assert "Встреча завершена" in resp.text


def test_stats_unknown_ident_stays_json(client) -> None:
    resp = client.get("/api/v1/urls/abc-def-ghi/stats")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
```

- [ ] **Step 2: Run them to confirm they FAIL**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener && uv run pytest tests/test_api.py -k "html or stays_json" -v`
Expected: FAIL — the redirect currently returns JSON (`content-type: application/json`), so the `text/html` assertions fail. (`test_stats_unknown_ident_stays_json` already passes — that's fine.)

- [ ] **Step 3: Update `routes.py`**

(a) Add `HTMLResponse` to the responses import. The current line is:
```python
from fastapi.responses import JSONResponse, RedirectResponse, Response
```
Replace with:
```python
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
```

(b) Import the pages — add near the other `event_shortener` imports:
```python
from event_shortener.pages import EXPIRED_PAGE, NOT_ACTIVE_PAGE, NOT_FOUND_PAGE
```

(c) Delete the now-unused `_within_window` helper (the whole function):
```python
def _within_window(record: ShortUrlDTO, now: datetime) -> bool:
    if record.not_before is not None and now < record.not_before:
        return False
    return not (record.expires_at is not None and now >= record.expires_at)
```
(After removing it, `ShortUrlDTO` may become an unused import in `routes.py` — if `ruff check` flags it, remove the `from event_shortener.dto.short_url import ShortUrlDTO` import too; if it's still used elsewhere, keep it.)

(d) Add a module-level constant near the top (after the imports):
```python
_NO_STORE = {"Cache-Control": "no-store"}
```

(e) Replace the entire `redirect` handler with:
```python
@redirect_router.get("/{ident}")
async def redirect(ident: str, controller: FromDishka[IShortenerController]) -> Response:
    """Public, unauthenticated redirect. 307 in-window, 410 outside it, 404 unknown.

    Error states render a minimal HTML page (browser-facing); API routes stay JSON.
    """
    record = await controller.resolve(ident)
    if record is None:
        metrics.REDIRECTS_TOTAL.labels(result="not_found").inc()
        return HTMLResponse(content=NOT_FOUND_PAGE, status_code=status.HTTP_404_NOT_FOUND, headers=_NO_STORE)

    now = datetime.now(UTC)
    if record.not_before is not None and now < record.not_before:
        metrics.REDIRECTS_TOTAL.labels(result="expired").inc()
        return HTMLResponse(content=NOT_ACTIVE_PAGE, status_code=status.HTTP_410_GONE, headers=_NO_STORE)
    if record.expires_at is not None and now >= record.expires_at:
        metrics.REDIRECTS_TOTAL.labels(result="expired").inc()
        return HTMLResponse(content=EXPIRED_PAGE, status_code=status.HTTP_410_GONE, headers=_NO_STORE)

    try:
        await controller.register_click(ident)
    except Exception:
        logger.exception("Failed to record click; serving redirect anyway", ident=ident)
    metrics.REDIRECTS_TOTAL.labels(result="ok").inc()
    return RedirectResponse(url=record.long_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
```

- [ ] **Step 4: Run the full suite + lint to confirm PASS**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener && uv run pytest -q && ruff check .`
Expected: all pass — the 4 new tests, plus the pre-existing redirect tests (`test_redirect_in_window_307`, `test_redirect_increments_click_count` stay 307; `test_expired_redirect_does_not_increment` still gets 410 and `click_count==0`). No lint errors. `HTTPException` is still imported/used by the `api_router` routes — leave that import.

- [ ] **Step 5: Commit (event-shortener repo)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener
git add event_shortener/routes.py tests/test_api.py
git commit --no-verify -m "feat(shortener): serve HTML pages on redirect 404/410

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Docs + live check

**Files:** `event-shortener/docs/API_CONTRACTS.md` (+ verification, no code)

- [ ] **Step 1: Docs** — in `event-shortener/docs/API_CONTRACTS.md`, note that the public redirect `GET /{ident}` returns a minimal HTML page (not JSON) for 404 (unknown) and the two 410 cases (not-yet-active / expired), with `Cache-Control: no-store`; API routes (incl. `/stats`) remain JSON.

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener
git add docs/API_CONTRACTS.md
git commit --no-verify -m "docs(shortener): HTML error pages on redirect 404/410

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 2: Live check** — rebuild and eyeball the three pages:
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose up -d --build event-shortener
KEY=dev-shortify-api-key-8c4e1f7b2a93d650
# 404:
docker compose exec -T event-shortener sh -c "curl -s -i http://localhost:8888/no-such-ident | head -20"
# 410 not-active (not_before in the future):
docker compose exec -T event-shortener sh -c "curl -s -X POST -H 'Authorization: Bearer $KEY' -H 'Content-Type: application/json' -d '{\"long_url\":\"https://x.example\",\"external_id\":\"page-early\",\"not_before\":4102444800,\"expires_at\":null}' http://localhost:8888/api/v1/urls/shorten"
# take the ident, then GET /<ident> and confirm the "ещё не активна" HTML + 410.
```
Confirm each returns the expected status, `Content-Type: text/html`, and the right Russian text. Record the outcome.

---

## Self-Review (against the spec)

**Spec coverage:**
- 404 unknown → HTML `NOT_FOUND_PAGE` (Task 2.3e). ✅
- 410 `now < not_before` → `NOT_ACTIVE_PAGE`; 410 `now >= expires_at` → `EXPIRED_PAGE` (Task 2.3e). ✅
- `Cache-Control: no-store` on all error responses (`_NO_STORE`, Task 2.3d/e + asserted in tests). ✅
- 307 in-window + click increment unchanged (Task 2.3e). ✅
- Metrics labels unchanged (`not_found`/`expired`/`ok`, Task 2.3e). ✅
- API/stats stay JSON (Task 2.1 `test_stats_unknown_ident_stays_json`). ✅
- Three minimalist pages, `lang="ru"`, inline CSS, no assets (Task 1.3). ✅
- Docs (Task 3). ✅

**Type/signature consistency:** `error_page(*, icon, title, message) -> str` (Task 1) produces `NOT_FOUND_PAGE`/`NOT_ACTIVE_PAGE`/`EXPIRED_PAGE`, imported and returned by the redirect handler (Task 2) — names identical. `HTMLResponse`/`_NO_STORE`/`status` all imported/defined before use.

**Placeholder scan:** none — every code step is the full text. The only conditional instruction (remove the `ShortUrlDTO` import iff ruff flags it unused) is a concrete ruff-driven check, not a placeholder.
