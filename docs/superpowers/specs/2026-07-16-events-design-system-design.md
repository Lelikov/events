# events-design-system — Design Spec

**Date:** 2026-07-16
**Status:** Approved-in-brainstorm (pending spec review)
**Context:** Prerequisite sub-project for slice 6.2 (`event-organizer-frontend`). Decomposed out of the 6.2 brainstorm.

## Goal

Extract the design layer currently living inside `event-admin-frontend` into a standalone, reusable npm package `events-design-system`, distributed as a git-tag dependency (mirroring `event-schemas`), and migrate all three existing SPAs (`event-admin-frontend`, `event-booker-frontend`, `jitsi-chat`) onto it so they share **one unified light visual language** — establishing a single source of truth before a fourth SPA (organizer, slice 6.2) is built. `jitsi-chat` drops its bespoke dark theme and joins the shared light design (per explicit decision).

## Motivation

The design has already diverged: `event-admin-frontend` carries a mature ~1400-line `index.css` design system plus reusable components, while `event-booker-frontend` (~120 lines) and `jitsi-chat` (~200 lines) grew their own bespoke, inconsistent styles. Building a fourth SPA (organizer) by copying admin's CSS would deepen the drift. Extracting a shared package and pointing every SPA at it stops the divergence and makes the organizer SPA (and any future frontend) a thin consumer.

## Distribution model (decided)

Mirror `event-schemas` exactly:

- Its **own git repository** `github.com/Lelikov/events-design-system`, also present as a directory `events-design-system/` in the monorepo with its own nested `.git` (like `event-schemas/`, `event-booking/`).
- Consumers depend on it by **git tag** in `package.json`:
  `"events-design-system": "github:Lelikov/events-design-system#v0.1.0"`.
- **Local development** swaps to a path/link dependency (`"events-design-system": "file:../events-design-system"`), mirroring the Python `{ path = "../event-schemas", editable = true }` swap. A `file:` dependency still runs the package's `prepare` build.
- No npm registry, no registry auth in any consumer CI. Accepted cost (same as `event-schemas`): a design change → new tag → bump + reinstall in each consumer.

## Package scope (decided: "CSS + generic components")

### Ships in the package

**CSS design system** — the generic core of admin's current `index.css`. Delivered primarily as one importable stylesheet, with the token layer also exported for future token-only needs. Every current consumer (admin, booker, jitsi) imports the full `styles.css`:

- `tokens.css` — only the `:root` custom properties (palette, semantic pales, shadows, radii, `--mono`, `--th-bg`, `--zebra`, `--row-hover`) **plus the Plus Jakarta Sans `@import`** and the base `font-family`/`font-size`/`line-height`. Imported by `styles.css`; also exported standalone (`./tokens.css`) for any future app that wants tokens without the opinionated element/component styles. No current consumer uses it alone.
- `styles.css` — the full opinionated stylesheet: `@import`s `tokens.css`, then reset, typography, and every generic component class. Selectors carried over verbatim: buttons (`button`, `.secondary`, `.icon-button`, `.link-button`, `.back-button`), forms (`.form`, `.field`, `.checkbox-field`, `.inline-actions`), `.card`, shell + sidebar (`.admin-shell`, `.app-sidebar`, `.app-brand`, `.app-nav*`, `.app-user*`, `.app-logout`, `.app-search`, legacy `.sidebar`/`.menu-item`), content/page structure (`.content`, `.stack`, `.page-header`, `.eyebrow`, `.breadcrumb`, `.muted`, `.error-text`, `.hint`), tables (`table`/`th`/`td`, zebra, sticky, `.table-wrap`, `.cell-*`, `.row-link`), `.tag`/`.tag-list`, semantic `.badge` + `.badge--*` (status + generic variants), `.uid-chip`/`.id-chip`, `.grid-2`/`.filters*`, `.list`/`.events-list`, `.kpi-*`, `.switch*`, `.seg`, `.modal-*`, `.user-info*`, `.picker*`, `.status-filter*`/`.status-option-btn`, `.tz-picker*`/`.tz-*`, `.results-count`/`.tabular`, animations (`fadeUp`), and the responsive `@media` blocks.

**Generic React components** (built to `dist/`):

- `Icon` — the existing SVG set, **extended** with icons the organizer SPA needs (`calendar`, `settings`, `user`, and any others 6.2 requires). Typed `IconName` union.
- `Switch` — carried over verbatim (presentational, accessible toggle).
- `ErrorBoundary` — carried over, **decoupled from Sentry**: it no longer imports `@sentry/react`; it accepts an optional `onError?: (error: Error, info: ErrorInfo) => void` prop. Consumers pass `onError={(e, i) => Sentry.captureException(e, { extra: { componentStack: i.componentStack } })}`. Its fallback UI keeps using design-system classes.
- `Badge` — **new** presentational component: `variant` + `children` props, renders `<span className={`badge badge--${variant}`}>`. `plain?` prop toggles `badge--plain`.
- `UserInfoView` — **new** presentational component: pure props (`name`, `email`, `variant: 'full' | 'name' | 'inline'`, optional `id`/`loading`), renders the `.user-info*` markup incl. initials. No data fetching.

### Stays in consumers (domain-coupled — NOT in the package)

- `StatusBadge` (maps booking status → label + variant; wraps the package's `Badge`).
- `UserInfo` with batch-loading from event-users (`userBatchLoader`); may render via the package's `UserInfoView`.
- `ParticipantPicker` (depends on `participantsApi`).
- App-specific CSS selectors kept in each consumer's local stylesheet: `.notif-*`, `.channel-*`, `.chat-event-icon`, `.event-status-icon`, `.timeline-*`, `.device-*`, `.badge--role-*`, `.code-area`, `.preview-box` (admin); `.booker-shell`, `.booker-card`, `.event-type-card`, `.slot-grid`/`.slot-button`, `.banner-error`, `.spinner` layout (booker); the meeting/chat **layout** (`.app-container`, `.video-section`, `.chat-section`, `.chat-toggle-btn`, `.toast-notification`, `.stub-container`, `.chat-loading`) recolored to light tokens, and the `stream-chat-react` CSS import switched to its light theme (jitsi).

### Not in the package (non-goals)

- App infrastructure: `routing.ts`, `api.ts`, `runtimeEnv.ts`, `observability/sentry.ts` stay per-consumer.
- No self-hosted fonts (keep the Google Fonts `@import`).
- No theming engine / dark-mode system, no CSS-modules / CSS-in-JS — stays flat global CSS.

## Package structure & build

```
events-design-system/
  package.json          # name "events-design-system"; peerDeps react/react-dom ^19;
                        # exports map; scripts: build (tsup) + prepare (tsup); test (vitest)
  tsup.config.ts        # entry src/index.ts → dist/ ESM + .d.ts
  src/
    index.ts            # re-exports Icon, Switch, ErrorBoundary, Badge, UserInfoView + IconName
    Icon.tsx
    Switch.tsx
    ErrorBoundary.tsx
    Badge.tsx
    UserInfoView.tsx
  styles/
    tokens.css
    styles.css          # @import './tokens.css'; …
  tests/                # vitest + happy-dom
  vitest.config.ts
  tsconfig.json
  eslint.config.js
  .github/workflows/ci.yml   # lint + typecheck + test + build (library-only, mirrors event-schemas)
  README.md
```

- **CSS** is shipped as static files (no build). `exports`: `"./styles.css"` → `./styles/styles.css`, `"./tokens.css"` → `./styles/tokens.css`.
- **Components** are pre-built with **tsup** (esbuild; transpiles TSX → ESM, emits `.d.ts`) into `dist/`, because a consumer's Rollup build does not transpile TSX from `node_modules`. `exports["."]` → `{ "types": "./dist/index.d.ts", "import": "./dist/index.js" }`.
- A **`prepare`** script runs `tsup` on install, so both git-tag and `file:` installs produce `dist/` (npm installs the package's devDeps when a `prepare` script is present). `dist/` is git-ignored (built on install / released via the tag's build).
- `peerDependencies`: `react` / `react-dom` `^19`. No Sentry dependency at all.

## Consumer migrations

Each migration is an independent, individually-testable task. Fidelity for admin is by construction; booker/jitsi are deliberate (mild) visual unification.

### event-admin-frontend (fidelity by construction)

- `styles.css` in the package = admin's generic core CSS **verbatim** (same rules, same source order). Admin keeps a thin local `app.css` with only the app-specific selectors listed above. Net cascade applied to admin is identical to today → visually identical by construction.
- `main.tsx`: `import 'events-design-system/styles.css'` then `import './app.css'` (replacing `import './index.css'`).
- `Icon`, `Switch`, `ErrorBoundary` now import from `events-design-system`; the local copies are deleted. `ErrorBoundary` usage gains `onError={Sentry.captureException-wrapper}`.
- `StatusBadge` / `UserInfo` stay, refactored to render via the package's `Badge` / `UserInfoView`.
- Admin's existing vitest suite must stay green (regression guard).

### event-booker-frontend (light — adopt full sheet)

- `main.tsx`: `import 'events-design-system/styles.css'` then its own local layout CSS.
- Drop bespoke button / `.field` input / `.booker-card` / error-banner styling in favor of design-system equivalents (`.card`, `.field`, `button`, `.error-text`); keep app-specific layout (`.booker-shell`, `.slot-grid`, `.slot-button`, `.event-type-card`, `.spinner`).
- Reconcile conflicts introduced by the design-system's global element selectors (e.g. global `button {}` now styles booker's buttons). Booker's existing vitest suite must stay green; the wizard UX (slot → guest form → confirmation) is unchanged, only restyled to the shared brand.

### jitsi-chat (light — full sheet, dark theme dropped)

- `main.tsx`: `import 'events-design-system/styles.css'` then its own local layout CSS (replacing the dark `layout.css` globals).
- **Drop the dark theme**: remove `--bg-color:#1a1a1a`/`--text-color:#fff` light-on-dark globals; the app chrome (chat panel `.chat-section`, header, `.chat-toggle-btn`, `.toast-notification`, `.stub-container`, `.chat-loading`, loading screens) is recolored to the shared light tokens (`--card`, `--bg`, `--text`, `--border`, `--primary`, `--danger`). The `.video-section` background stays black — video tiles are inherently dark, which is not a "theme".
- Switch the `stream-chat-react` CSS to its **light** theme (`str-chat__theme-light`) so the embedded chat matches.
- Keep the meeting/chat **layout** (`.app-container`, `.video-section`, `.chat-section`, responsive `@media`), only recolored. The video-first UX and stream-chat integration must not break. Jitsi's build must stay green; the visual change (dark → light) is intended and verified by manual smoke.

## Versioning & CI

- First release tag `v0.1.0`. Each consumer pins the tag in `package.json`.
- Package repo CI (GitHub Actions): `npm ci` → lint (eslint) → typecheck (`tsc --noEmit`) → test (vitest) → build (`tsup`). Library-only, no image publish — mirrors `event-schemas`'s lint/test CI.
- Consumer CIs are unchanged in shape; they just resolve the git-tag dependency during install.

## Testing

- **Package:** vitest + happy-dom for the five components:
  - `Icon` renders an `<svg>` for a given name; unknown name handled.
  - `Badge` applies `badge badge--<variant>` and `badge--plain` when `plain`.
  - `Switch` toggles `is-on` and calls `onChange` with the negated value; `disabled` blocks it.
  - `ErrorBoundary` catches a thrown render error, shows the fallback, and calls `onError` with the error + component stack.
  - `UserInfoView` renders name/email/initials per `variant`.
- **Consumers:** each SPA's existing vitest suite stays green (no logic changes). Visual fidelity for admin is by construction; booker/jitsi visual changes are verified by a manual smoke (bring the SPA up, confirm the intended unified look and unbroken layout).

## Rollout order

1. Build & publish `events-design-system` (`v0.1.0`): package scaffold, `tokens.css` + `styles.css` extracted verbatim from admin, five components, tests, CI, tag.
2. Migrate `event-admin-frontend` (fidelity) — proves the extraction.
3. Migrate `event-booker-frontend` (light, full sheet).
4. Migrate `jitsi-chat` (light, full sheet — dark theme dropped, stream-chat switched to light).

Each consumer migration bumps its `package.json` to the `v0.1.0` tag (or `file:` link during local dev), then is validated independently. A design fix discovered during a later migration → patch the package, new tag, bump the already-migrated consumers.

## Out of scope / deferred

- The organizer SPA itself (slice 6.2) — its own spec, consuming this package.
- Self-hosted fonts, dark-mode theming, component-level theming APIs.
- Moving app infrastructure (routing/api/env/sentry-setup) into the package.
- Storybook / a component gallery site.
