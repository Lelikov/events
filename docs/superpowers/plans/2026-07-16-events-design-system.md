# events-design-system Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the design layer from `event-admin-frontend` into a standalone git-tag npm package `events-design-system`, then migrate `event-admin-frontend`, `event-booker-frontend`, and `jitsi-chat` onto it so all SPAs share one unified light visual language.

**Architecture:** A new package `events-design-system/` ships two plain CSS files (`tokens.css` + full `styles.css`, extracted verbatim from admin's `index.css` generic core) and five generic React components (`Icon`, `Switch`, `ErrorBoundary`, `Badge`, `UserInfoView`) pre-built with `tsup` to `dist/`. Consumers depend on it via a local `file:../events-design-system` link during this work and cut over to a `github:...#v0.1.0` git tag at the end (mirroring `event-schemas`). Admin migration is fidelity-preserving by construction; booker and jitsi adopt the full light sheet (jitsi drops its dark theme).

**Tech Stack:** TypeScript ~5.9, React ^19, tsup (esbuild), Vitest 4 + happy-dom, ESLint flat config, plain global CSS with CSS-variable tokens, Plus Jakarta Sans via Google Fonts `@import`.

## Global Constraints

- **Package name:** `events-design-system`. **Peer deps:** `react` / `react-dom` `^19` (never a hard dependency). **No Sentry dependency anywhere in the package.**
- **Distribution:** during this plan, consumers reference the package as `"events-design-system": "file:../events-design-system"`. The final task cuts over to `"events-design-system": "github:Lelikov/events-design-system#v0.1.0"` (mirrors `event-schemas`).
- **Build:** components compile with `tsup` (ESM + `.d.ts`) to `dist/`; a `prepare` script builds on install. `dist/` is git-ignored. CSS files are shipped as-is (no build).
- **`exports` map (exact):** `"."` → `{ "types": "./dist/index.d.ts", "import": "./dist/index.js" }`; `"./styles.css"` → `"./styles/styles.css"`; `"./tokens.css"` → `"./styles/tokens.css"`.
- **Commit locations (each is a separate git repo/root):** `events-design-system/` → its own new git repo (`git init` in Task 1). `event-admin-frontend/` → its own nested repo. `jitsi-chat/` → its own nested repo. `event-booker-frontend/` → the **root** `events` repo (it is root-tracked, no nested `.git`). Run each task's `git commit` from inside the correct directory.
- **CSS is plain global CSS.** Keep the `@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:...')` at the top of `tokens.css`. No CSS-modules, no CSS-in-JS, no theming engine.
- **Fidelity rule (admin):** the set and source-order of CSS rules applied to `event-admin-frontend` after migration must equal today's. Package `styles.css` = admin's generic core verbatim; admin keeps only app-specific selectors in a local `app.css`.
- **Intended visual change:** `event-booker-frontend` and `jitsi-chat` adopt the light brand; jitsi's dark theme is removed. This is deliberate, verified by manual smoke.
- **TS style:** follow the existing frontends' conventions (function components, early returns, `.ts`/`.tsx` extension imports, `type`-only imports where the code uses them). ESLint config mirrors admin's flat config.
- **Source of truth for the extracted CSS:** `event-admin-frontend/src/index.css` (1398 lines) as it exists at plan time. Copy byte-for-byte; do not reformat.

---

### Task 1: Scaffold `events-design-system` package + build/lint/test pipeline + `Badge`

Establishes the whole toolchain end-to-end by shipping the smallest real component (`Badge`) through it.

**Files:**
- Create: `events-design-system/package.json`
- Create: `events-design-system/tsup.config.ts`
- Create: `events-design-system/tsconfig.json`
- Create: `events-design-system/eslint.config.js`
- Create: `events-design-system/vitest.config.ts`
- Create: `events-design-system/.gitignore`
- Create: `events-design-system/README.md`
- Create: `events-design-system/src/index.ts`
- Create: `events-design-system/src/Badge.tsx`
- Test: `events-design-system/tests/Badge.test.tsx`

**Interfaces:**
- Produces: `Badge` component — `type BadgeVariant = 'created' | 'confirmed' | 'in_progress' | 'completed' | 'cancelled' | 'rescheduled' | 'neutral' | 'danger' | 'warning' | 'success' | 'muted'`; `function Badge(props: { variant: BadgeVariant; plain?: boolean; children: ReactNode }): JSX.Element` renders `<span className="badge badge--<variant>">` (adds ` badge--plain` when `plain`).
- Produces: build/test/lint scripts every later package task reuses.

- [ ] **Step 1: Create `events-design-system/package.json`**

```json
{
  "name": "events-design-system",
  "version": "0.1.0",
  "type": "module",
  "files": ["dist", "styles"],
  "exports": {
    ".": { "types": "./dist/index.d.ts", "import": "./dist/index.js" },
    "./styles.css": "./styles/styles.css",
    "./tokens.css": "./styles/tokens.css"
  },
  "scripts": {
    "build": "tsup",
    "prepare": "tsup",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "peerDependencies": {
    "react": "^19.2.4",
    "react-dom": "^19.2.4"
  },
  "devDependencies": {
    "@eslint/js": "^9.39.4",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.0",
    "eslint": "^9.39.4",
    "eslint-plugin-react-hooks": "^7.0.1",
    "globals": "^17.4.0",
    "happy-dom": "^20.10.2",
    "react": "^19.2.4",
    "react-dom": "^19.2.4",
    "tsup": "^8.3.0",
    "typescript": "~5.9.3",
    "typescript-eslint": "^8.56.1",
    "vitest": "^4.1.8"
  }
}
```

- [ ] **Step 2: Create `events-design-system/tsup.config.ts`**

```ts
import { defineConfig } from 'tsup'

export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm'],
  dts: true,
  clean: true,
  external: ['react', 'react-dom', 'react/jsx-runtime'],
})
```

- [ ] **Step 3: Create `events-design-system/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2023",
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "skipLibCheck": true,
    "declaration": true,
    "emitDeclarationOnly": false,
    "noEmit": true,
    "verbatimModuleSyntax": true
  },
  "include": ["src", "tests"]
}
```

- [ ] **Step 4: Create `events-design-system/eslint.config.js`**

```js
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, tseslint.configs.recommended, reactHooks.configs.flat.recommended],
    languageOptions: { ecmaVersion: 2020, globals: globals.browser },
  },
])
```

- [ ] **Step 5: Create `events-design-system/vitest.config.ts`**

```ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: { environment: 'happy-dom' },
})
```

- [ ] **Step 6: Create `events-design-system/.gitignore`**

```
node_modules
dist
*.tsbuildinfo
```

- [ ] **Step 7: Create `events-design-system/README.md`**

```md
# events-design-system

Shared design system for the events SPAs (admin, booker, organizer, jitsi-chat).

- `import 'events-design-system/styles.css'` — full light stylesheet (tokens + reset + components).
- `import 'events-design-system/tokens.css'` — CSS variables + font only.
- `import { Icon, Switch, ErrorBoundary, Badge, UserInfoView } from 'events-design-system'` — generic components.

Distributed as a git-tag dependency (`github:Lelikov/events-design-system#vX.Y.Z`); use `file:../events-design-system` for local development. `dist/` is built by `tsup` on install.
```

- [ ] **Step 8: Create `events-design-system/src/Badge.tsx`**

```tsx
import type { ReactNode } from 'react'

export type BadgeVariant =
  | 'created'
  | 'confirmed'
  | 'in_progress'
  | 'completed'
  | 'cancelled'
  | 'rescheduled'
  | 'neutral'
  | 'danger'
  | 'warning'
  | 'success'
  | 'muted'

type Props = {
  variant: BadgeVariant
  plain?: boolean
  children: ReactNode
}

/** Presentational status pill. Colour comes from `.badge--<variant>` in styles.css. */
export function Badge({ variant, plain = false, children }: Props) {
  return <span className={`badge badge--${variant}${plain ? ' badge--plain' : ''}`}>{children}</span>
}
```

- [ ] **Step 9: Create `events-design-system/src/index.ts`**

```ts
export { Badge } from './Badge.tsx'
export type { BadgeVariant } from './Badge.tsx'
```

- [ ] **Step 10: Write the failing test `events-design-system/tests/Badge.test.tsx`**

```tsx
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Badge } from '../src/index.ts'

describe('Badge', () => {
  it('applies the variant class', () => {
    const { container } = render(<Badge variant="confirmed">Ok</Badge>)
    const span = container.querySelector('span')!
    expect(span.className).toBe('badge badge--confirmed')
    expect(span.textContent).toBe('Ok')
  })

  it('adds badge--plain when plain', () => {
    const { container } = render(<Badge variant="neutral" plain>N</Badge>)
    expect(container.querySelector('span')!.className).toBe('badge badge--neutral badge--plain')
  })
})
```

Add `@testing-library/react` to `devDependencies` (`^16.1.0`) — it is the render helper used by all component tests in this package.

- [ ] **Step 11: Install + verify the test fails, then passes**

Run: `cd events-design-system && npm install`
Run: `npm test`
Expected: 2 passed.

- [ ] **Step 12: Verify build, typecheck, lint**

Run: `npm run build && npm run typecheck && npm run lint`
Expected: `dist/index.js` + `dist/index.d.ts` emitted; typecheck clean; lint clean.

- [ ] **Step 13: Init the package git repo and commit**

```bash
cd events-design-system
git init -q
git add -A
git commit -q -m "chore: scaffold events-design-system package + Badge"
```

---

### Task 2: Extract `tokens.css` + `styles.css` from admin's `index.css`

**Files:**
- Create: `events-design-system/styles/tokens.css`
- Create: `events-design-system/styles/styles.css`
- Test: `events-design-system/tests/styles.test.ts`
- Reference (read-only): `event-admin-frontend/src/index.css`

**Interfaces:**
- Produces: `events-design-system/styles/styles.css` (importable via `events-design-system/styles.css`) and `events-design-system/styles/tokens.css` (via `events-design-system/tokens.css`). Later admin migration relies on `styles.css` containing the generic core verbatim.

- [ ] **Step 1: Create `events-design-system/styles/tokens.css`** (the two `:root` token blocks from admin `index.css` merged, plus the font import — verbatim values)

```css
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,300&display=swap');

/* ─────────────────────────────────────────────
   TOKENS
───────────────────────────────────────────── */
:root {
  --bg:           #f3f5fa;
  --bg-soft:      #ffffff;
  --card:         #ffffff;
  --text:         #111827;
  --text-2:       #374151;
  --muted:        #6b7280;
  --border:       #e4e8f0;
  --primary:      #4f6ef2;
  --primary-dark: #3d5ae3;
  --primary-pale: #eef1fd;
  --danger:       #dc3545;
  --danger-pale:  #fff0f2;
  --success:      #16a34a;
  --success-pale: #ecfdf5;
  --shadow-sm:    0 1px 2px rgba(0,0,0,0.06);
  --shadow:       0 1px 3px rgba(0,0,0,0.07), 0 6px 20px rgba(60,90,160,0.07);
  --radius:       10px;
  --radius-sm:    7px;

  --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
  --radius-lg: 13px;
  --th-bg: #fafbfd;
  --zebra: #fafbfd;
  --row-hover: #eef1fd;

  font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}
```

- [ ] **Step 2: Create `events-design-system/styles/styles.css`** — start with the import, then paste the entire generic core of admin `index.css` verbatim, MINUS the two `:root` blocks (now in tokens.css) and MINUS the app-specific selectors listed below.

The first line is exactly:

```css
@import './tokens.css';
```

Then copy, in original source order, every rule from `event-admin-frontend/src/index.css` EXCEPT:
- the two `:root { … }` token blocks (lines ~12–35 and ~938–944) — already moved to `tokens.css`;
- the following **app-specific** selectors, which stay in each consumer (do NOT copy them into the package): `.chat-event-icon`, `.event-status-icon` (and its `.is-positive`/`.is-negative`/`.is-neutral` modifiers), `.timeline`, `.timeline-item`, `.timeline-item-organizer`, `.timeline-item-client`, `.timeline-time`, `.device-access-list`, `.device-list`, `.badge--role-organizer`, `.badge--role-client`, the notifications block (`.seg`, `.notif-toolbar`, `.notif-list`, `.notif-card`, `.notif-card-head`, `.notif-title`, `.notif-code`, `.channel-grid`, `.channel`, `.channel-head`, `.channel-name`, `.code-area`, `.preview-box`) and its trailing `@media (max-width: 960px){ .channel-grid … }`.

Keep EVERYTHING else verbatim, including: reset, typography, buttons, forms, `.login-*`, `.admin-shell`, `.sidebar*`, `.menu*`, `.content`, `.card`, `.stack`, `.page-header`, `.eyebrow`, `.muted`, `.error-text`, `.hint`, tables, `.tag*`, `.grid-2`, `.filters*`, `.list`, `.events-list`, `.results-count`, `.tabular`, `.picker*`, `.status-filter*`, `.status-option-btn`, `.tz-picker*`, `.tz-*`, `@keyframes fadeUp`, `.user-info*`, the responsive `@media (max-width: 960px)` shell block, `.modal-*`, `button.small`, and the entire "REDESIGN — 2026 refresh" section (`.breadcrumb`, `.badge`, `.badge--*` status/generic variants, `.uid-chip`, `.id-chip`, table zebra/sticky, `.cell-*`, `.row-link`, `.kpi-*`, `.switch*`, `.app-sidebar` and all `.app-*`, `.login-split`/`.login-brand*`/`.login-form-panel`, and its `@media` blocks) EXCEPT the `.badge--role-*` and `.seg`/notifications rules noted above.

> Note: `.seg` (segmented control) is admin-notifications-specific per the spec — leave it in admin. The organizer SPA does not need it.

- [ ] **Step 3: Write the guard test `events-design-system/tests/styles.test.ts`** (protects against accidental truncation and confirms the app-specific exclusions are absent)

```ts
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const tokens = readFileSync(fileURLToPath(new URL('../styles/tokens.css', import.meta.url)), 'utf8')
const styles = readFileSync(fileURLToPath(new URL('../styles/styles.css', import.meta.url)), 'utf8')

describe('tokens.css', () => {
  it('defines the core tokens and the font import', () => {
    for (const token of ['--primary:', '--bg:', '--danger:', '--radius:', '--radius-lg:', '--mono:', '--row-hover:']) {
      expect(tokens).toContain(token)
    }
    expect(tokens).toContain('Plus+Jakarta+Sans')
  })
})

describe('styles.css', () => {
  it('imports tokens and carries the generic component core', () => {
    expect(styles).toContain("@import './tokens.css'")
    for (const sel of ['.card', '.app-sidebar', '.badge', '.kpi-card', '.switch', '.user-info', '.picker', '.tz-picker', '.modal-overlay', 'table']) {
      expect(styles).toContain(sel)
    }
  })

  it('excludes app-specific selectors that belong to consumers', () => {
    for (const sel of ['.chat-event-icon', '.timeline-item', '.notif-card', '.channel-grid', '.badge--role-organizer', '.device-access-list']) {
      expect(styles).not.toContain(sel)
    }
  })

  it('does not redeclare the token :root block (tokens live in tokens.css)', () => {
    expect(styles).not.toContain('--primary:')
  })
})
```

- [ ] **Step 4: Run the guard test**

Run: `cd events-design-system && npm test`
Expected: all pass (Badge + styles). If the "excludes" assertion fails, an app-specific selector leaked into `styles.css`; if "does not redeclare" fails, a `:root` block was copied by mistake.

- [ ] **Step 5: Commit**

```bash
cd events-design-system
git add styles/tokens.css styles/styles.css tests/styles.test.ts
git commit -q -m "feat: extract tokens.css + styles.css from admin index.css"
```

---

### Task 3: Components — `Icon` (extended), `Switch`, `ErrorBoundary` (Sentry-decoupled), `UserInfoView`

**Files:**
- Create: `events-design-system/src/Icon.tsx`
- Create: `events-design-system/src/Switch.tsx`
- Create: `events-design-system/src/ErrorBoundary.tsx`
- Create: `events-design-system/src/UserInfoView.tsx`
- Modify: `events-design-system/src/index.ts`
- Test: `events-design-system/tests/Icon.test.tsx`, `Switch.test.tsx`, `ErrorBoundary.test.tsx`, `UserInfoView.test.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `type IconName` (adds `'calendar' | 'settings' | 'user'` to admin's set) and `function Icon(props: SVGProps<SVGSVGElement> & { name: IconName; size?: number })`.
  - `function Switch(props: { checked: boolean; onChange: (next: boolean) => void; disabled?: boolean; label?: string; showState?: boolean })`.
  - `class ErrorBoundary` — props `{ children: ReactNode; onError?: (error: Error, info: ErrorInfo) => void; homeHref?: string }` (default `homeHref='/'`); no Sentry import.
  - `function UserInfoView(props: { name: string | null; email: string; id?: string; variant?: 'full' | 'name' | 'inline'; loading?: boolean })`.

- [ ] **Step 1: Create `events-design-system/src/Icon.tsx`** (admin's Icon verbatim, with three names added to the union and `PATHS`)

```tsx
import type { ReactElement, SVGProps } from 'react'

export type IconName =
  | 'dashboard'
  | 'bookings'
  | 'users'
  | 'blacklist'
  | 'notifications'
  | 'search'
  | 'clock'
  | 'logout'
  | 'refresh'
  | 'download'
  | 'plus'
  | 'edit'
  | 'trash'
  | 'chevron-left'
  | 'calendar'
  | 'settings'
  | 'user'

const PATHS: Record<IconName, ReactElement> = {
  dashboard: (
    <>
      <rect x="1.5" y="1.5" width="6" height="6" rx="1.8" />
      <rect x="10.5" y="1.5" width="6" height="6" rx="1.8" />
      <rect x="1.5" y="10.5" width="6" height="6" rx="1.8" />
      <rect x="10.5" y="10.5" width="6" height="6" rx="1.8" />
    </>
  ),
  bookings: (
    <>
      <rect x="2" y="3.5" width="14" height="12.5" rx="2.2" />
      <line x1="2" y1="7.2" x2="16" y2="7.2" />
      <line x1="6" y1="1.6" x2="6" y2="4.4" strokeLinecap="round" />
      <line x1="12" y1="1.6" x2="12" y2="4.4" strokeLinecap="round" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="6" r="3.1" />
      <path d="M3.5 15.5c0-3.1 2.6-4.7 5.5-4.7s5.5 1.6 5.5 4.7" strokeLinecap="round" />
    </>
  ),
  blacklist: (
    <>
      <circle cx="9" cy="9" r="6.6" />
      <line x1="4.4" y1="4.4" x2="13.6" y2="13.6" strokeLinecap="round" />
    </>
  ),
  notifications: (
    <>
      <circle cx="9" cy="9" r="6.6" />
      <circle cx="9" cy="9" r="2.2" fill="currentColor" stroke="none" />
    </>
  ),
  search: (
    <>
      <circle cx="7" cy="7" r="5" />
      <line x1="11" y1="11" x2="14.5" y2="14.5" strokeLinecap="round" />
    </>
  ),
  clock: (
    <>
      <circle cx="9" cy="9" r="6.5" />
      <path d="M9 5v4l2.6 1.6" strokeLinecap="round" />
    </>
  ),
  logout: (
    <path d="M6.5 2.5H3.5v13H6.5M11 12l3.5-3.5L11 5M14.5 8.5H7" strokeLinecap="round" strokeLinejoin="round" />
  ),
  refresh: (
    <path d="M15 9a6 6 0 1 1-1.8-4.3M15 2v3.5h-3.5" strokeLinecap="round" strokeLinejoin="round" />
  ),
  download: (
    <path d="M9 2.5v8.5m0 0L5.5 7.5M9 11l3.5-3.5M3.5 14.5h11" strokeLinecap="round" strokeLinejoin="round" />
  ),
  plus: <path d="M9 3.5v11M3.5 9h11" strokeLinecap="round" />,
  edit: <path d="M12 2.5 15.5 6 6 15.5l-4 .5.5-4L12 2.5Z" strokeLinejoin="round" />,
  trash: (
    <path d="M3 4.5h12M7 4V2.5h4V4M5.5 4.5l.5 11h6l.5-11" strokeLinecap="round" strokeLinejoin="round" />
  ),
  'chevron-left': <path d="M11 3.5 6 9l5 5.5" strokeLinecap="round" strokeLinejoin="round" />,
  calendar: (
    <>
      <rect x="2.5" y="3.5" width="13" height="12" rx="2" />
      <line x1="2.5" y1="7" x2="15.5" y2="7" />
      <line x1="6" y1="1.8" x2="6" y2="4.2" strokeLinecap="round" />
      <line x1="12" y1="1.8" x2="12" y2="4.2" strokeLinecap="round" />
    </>
  ),
  settings: (
    <>
      <circle cx="9" cy="9" r="2.4" />
      <path
        d="M9 1.8v2M9 14.2v2M16.2 9h-2M3.8 9h-2M13.9 4.1l-1.4 1.4M5.5 12.5l-1.4 1.4M13.9 13.9l-1.4-1.4M5.5 5.5 4.1 4.1"
        strokeLinecap="round"
      />
    </>
  ),
  user: (
    <>
      <circle cx="9" cy="6" r="3.1" />
      <path d="M3.5 15.5c0-3.1 2.6-4.7 5.5-4.7s5.5 1.6 5.5 4.7" strokeLinecap="round" />
    </>
  ),
}

type Props = SVGProps<SVGSVGElement> & {
  name: IconName
  size?: number
}

export function Icon({ name, size = 18, ...rest }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 18 18"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      aria-hidden="true"
      {...rest}
    >
      {PATHS[name]}
    </svg>
  )
}
```

- [ ] **Step 2: Create `events-design-system/src/Switch.tsx`** (admin's Switch verbatim)

```tsx
type Props = {
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  /** Accessible label; also used for the title tooltip. */
  label?: string
  /** Show a "Вкл / Выкл" text next to the track. */
  showState?: boolean
}

/** Compact accessible toggle. */
export function Switch({ checked, onChange, disabled = false, label, showState = false }: Props) {
  const button = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label ?? (checked ? 'Выключить' : 'Включить')}
      className={`switch${checked ? ' is-on' : ''}`}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    />
  )

  if (!showState) return button

  return (
    <span className="switch-field">
      <span className={`switch-state ${checked ? 'is-on' : 'is-off'}`}>{checked ? 'Вкл' : 'Выкл'}</span>
      {button}
    </span>
  )
}
```

- [ ] **Step 3: Create `events-design-system/src/ErrorBoundary.tsx`** (admin's, with Sentry removed and an `onError` prop + `homeHref`)

```tsx
import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = {
  children: ReactNode
  /** Called on a caught render error. Consumers wire Sentry here. */
  onError?: (error: Error, info: ErrorInfo) => void
  /** Where the "reload" button navigates. */
  homeHref?: string
}

type State = {
  error: Error | null
}

/**
 * Last-resort boundary: without it any render-time exception unmounts the
 * whole React tree and leaves a blank page with no way to recover.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.props.onError?.(error, info)
    console.error('Unhandled render error', error, info)
  }

  render(): ReactNode {
    if (!this.state.error) {
      return this.props.children
    }

    return (
      <main className="login-shell">
        <section className="login-card">
          <h1>Что-то пошло не так</h1>
          <p className="muted">{this.state.error.message}</p>
          <div className="inline-actions">
            <button type="button" onClick={() => window.location.assign(this.props.homeHref ?? '/')}>
              Перезагрузить
            </button>
          </div>
        </section>
      </main>
    )
  }
}
```

- [ ] **Step 4: Create `events-design-system/src/UserInfoView.tsx`** (presentational, derived from admin's `UserInfo` render logic; no fetching)

```tsx
type Props = {
  name: string | null
  email: string
  id?: string
  variant?: 'full' | 'name' | 'inline'
  loading?: boolean
}

function initials(name: string | null, email: string): string {
  if (!name) return email[0]?.toUpperCase() ?? '?'
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('')
}

/** Pure presentation of a user identity. Data loading stays in the consumer. */
export function UserInfoView({ name, email, id, variant = 'full', loading = false }: Props) {
  if (loading) {
    return <span className="user-info-loading">{id ? <span className="user-info-id">{id}</span> : '…'}</span>
  }

  if (variant === 'name') {
    if (!name) return <span className="user-info-name-only">{email}</span>
    return <span className="user-info-name-only">{name}</span>
  }

  if (variant === 'inline') {
    if (!name) return <span className="user-info-name-only">{email}</span>
    return (
      <span className="user-info-inline">
        <span className="user-info-inline-name">{name}</span>
        <span className="user-info-inline-sep"> · </span>
        <span className="user-info-inline-email">{email}</span>
      </span>
    )
  }

  return (
    <span className="user-info">
      <span className="user-info-avatar">{initials(name, email)}</span>
      <span className="user-info-details">
        <span className="user-info-primary">{name ?? email}</span>
        {name && <span className="user-info-secondary">{email}</span>}
      </span>
    </span>
  )
}
```

- [ ] **Step 5: Update `events-design-system/src/index.ts`**

```ts
export { Badge } from './Badge.tsx'
export type { BadgeVariant } from './Badge.tsx'
export { Icon } from './Icon.tsx'
export type { IconName } from './Icon.tsx'
export { Switch } from './Switch.tsx'
export { ErrorBoundary } from './ErrorBoundary.tsx'
export { UserInfoView } from './UserInfoView.tsx'
```

- [ ] **Step 6: Write tests**

`events-design-system/tests/Icon.test.tsx`:

```tsx
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Icon } from '../src/index.ts'

describe('Icon', () => {
  it('renders an svg for a known name', () => {
    const { container } = render(<Icon name="calendar" />)
    const svg = container.querySelector('svg')!
    expect(svg).toBeTruthy()
    expect(svg.getAttribute('width')).toBe('18')
    expect(svg.querySelector('rect')).toBeTruthy()
  })

  it('honours size and passes through svg props', () => {
    const { container } = render(<Icon name="settings" size={24} className="x" />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('width')).toBe('24')
    expect(svg.getAttribute('class')).toBe('x')
  })
})
```

`events-design-system/tests/Switch.test.tsx`:

```tsx
import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Switch } from '../src/index.ts'

describe('Switch', () => {
  it('reflects checked and toggles to the negated value', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<Switch checked={false} onChange={onChange} />)
    const btn = getByRole('switch')
    expect(btn.className).toBe('switch')
    fireEvent.click(btn)
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('does not fire when disabled', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<Switch checked disabled onChange={onChange} />)
    fireEvent.click(getByRole('switch'))
    expect(onChange).not.toHaveBeenCalled()
  })
})
```

`events-design-system/tests/ErrorBoundary.test.tsx`:

```tsx
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from '../src/index.ts'

function Boom(): never {
  throw new Error('kaboom')
}

describe('ErrorBoundary', () => {
  it('renders the fallback and calls onError on a child error', () => {
    const onError = vi.fn()
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { getByText } = render(
      <ErrorBoundary onError={onError}>
        <Boom />
      </ErrorBoundary>,
    )
    expect(getByText('Что-то пошло не так')).toBeTruthy()
    expect(onError).toHaveBeenCalledOnce()
    expect(onError.mock.calls[0][0]).toBeInstanceOf(Error)
    spy.mockRestore()
  })

  it('renders children when there is no error', () => {
    const { getByText } = render(
      <ErrorBoundary>
        <span>ok</span>
      </ErrorBoundary>,
    )
    expect(getByText('ok')).toBeTruthy()
  })
})
```

`events-design-system/tests/UserInfoView.test.tsx`:

```tsx
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { UserInfoView } from '../src/index.ts'

describe('UserInfoView', () => {
  it('renders initials + name + email in full variant', () => {
    const { container } = render(<UserInfoView name="Ada Lovelace" email="ada@x.io" />)
    expect(container.querySelector('.user-info-avatar')!.textContent).toBe('AL')
    expect(container.querySelector('.user-info-primary')!.textContent).toBe('Ada Lovelace')
    expect(container.querySelector('.user-info-secondary')!.textContent).toBe('ada@x.io')
  })

  it('falls back to email when no name', () => {
    const { container } = render(<UserInfoView name={null} email="x@y.io" variant="inline" />)
    expect(container.querySelector('.user-info-name-only')!.textContent).toBe('x@y.io')
  })
})
```

- [ ] **Step 7: Run tests, build, typecheck, lint**

Run: `cd events-design-system && npm test && npm run build && npm run typecheck && npm run lint`
Expected: all component tests + styles + Badge pass; `dist/` rebuilt; typecheck + lint clean.

- [ ] **Step 8: Commit**

```bash
cd events-design-system
git add src tests
git commit -q -m "feat: Icon (extended) + Switch + Sentry-free ErrorBoundary + UserInfoView"
```

---

### Task 4: Migrate `event-admin-frontend` CSS onto the package (fidelity)

Splits admin's `index.css` into "package styles.css (already extracted)" + "local app.css (app-specific selectors only)"; nets an identical cascade.

**Files:**
- Modify: `event-admin-frontend/package.json` (add `file:` dep)
- Create: `event-admin-frontend/src/app.css` (app-specific selectors only)
- Delete: `event-admin-frontend/src/index.css`
- Modify: `event-admin-frontend/src/main.tsx` (import order)

**Interfaces:**
- Consumes: `events-design-system/styles.css`.
- Produces: admin renders with the shared stylesheet + its own `app.css`.

- [ ] **Step 1: Add the local package dependency**

Edit `event-admin-frontend/package.json` — add to `dependencies`: `"events-design-system": "file:../events-design-system"`. Then:

Run: `cd event-admin-frontend && npm install`
Expected: `events-design-system` linked; its `prepare` builds `dist/`.

- [ ] **Step 2: Create `event-admin-frontend/src/app.css`** with ONLY the app-specific selectors removed from the package (copy them verbatim from the current `index.css`): `.chat-event-icon`, `.event-status-icon` + modifiers, `.timeline*`, `.device-access-list`, `.device-list`, `.badge--role-organizer`, `.badge--role-client`, and the whole notifications block (`.seg`, `.notif-*`, `.channel*`, `.code-area`, `.preview-box`) including its trailing `@media (max-width: 960px){ .channel-grid { grid-template-columns: 1fr } }`.

- [ ] **Step 3: Update `event-admin-frontend/src/main.tsx`** import lines — replace `import './index.css'` with the two imports (order matters: package first, then local overrides):

```tsx
import 'events-design-system/styles.css'
import './app.css'
```

- [ ] **Step 4: Delete `event-admin-frontend/src/index.css`**

```bash
cd event-admin-frontend && git rm src/index.css
```

- [ ] **Step 5: Build + run the existing admin test suite (regression guard)**

Run: `cd event-admin-frontend && npm run build && npm test`
Expected: build succeeds; existing vitest suite green (no logic changed).

- [ ] **Step 6: Manual fidelity smoke**

Run: `cd event-admin-frontend && npm run dev` — open the app, confirm sidebar, tables, badges, KPI cards, notifications page render exactly as before. (Notifications `.seg`/channel cards now come from `app.css`.)

- [ ] **Step 7: Commit (in the admin repo)**

```bash
cd event-admin-frontend
git add package.json package-lock.json src/app.css src/main.tsx
git commit -m "refactor: consume events-design-system styles; keep app-specific css local"
```

---

### Task 5: Migrate `event-admin-frontend` components onto the package

**Files:**
- Delete: `event-admin-frontend/src/modules/shared/Icon.tsx`, `Switch.tsx`, `ErrorBoundary.tsx`
- Modify: `event-admin-frontend/src/modules/shared/StatusBadge.tsx` (render via package `Badge`)
- Modify: `event-admin-frontend/src/modules/shared/UserInfo.tsx` (render via package `UserInfoView`)
- Modify: `event-admin-frontend/src/main.tsx` (ErrorBoundary import + `onError`)
- Modify: every admin file importing the three moved components (update import paths)

**Interfaces:**
- Consumes: `Icon`, `Switch`, `ErrorBoundary`, `Badge`, `UserInfoView` from `events-design-system`.

- [ ] **Step 1: Find all importers of the three moved components**

Run: `cd event-admin-frontend && grep -rln "shared/Icon\|shared/Switch\|shared/ErrorBoundary" src`
Expected: a list of `.tsx` files (incl. `main.tsx`, `AdminLayout.tsx`, notification/blacklist pages).

- [ ] **Step 2: Delete the three local component files**

```bash
cd event-admin-frontend && git rm src/modules/shared/Icon.tsx src/modules/shared/Switch.tsx src/modules/shared/ErrorBoundary.tsx
```

- [ ] **Step 3: Repoint every importer** from `'.../shared/Icon.tsx'` / `Switch.tsx` / `ErrorBoundary.tsx` to `'events-design-system'`. Example — in `main.tsx`, replace `import { ErrorBoundary } from './modules/shared/ErrorBoundary.tsx'` with `import { ErrorBoundary } from 'events-design-system'` and wire Sentry via `onError`:

```tsx
import * as Sentry from '@sentry/react'
// …
<ErrorBoundary onError={(e, info) => Sentry.captureException(e, { extra: { componentStack: info.componentStack } })} homeHref="/dashboard">
```

For `Icon` / `Switch` importers, change only the module specifier to `'events-design-system'` (named imports unchanged).

- [ ] **Step 4: Refactor `StatusBadge.tsx` to wrap the package `Badge`**

```tsx
import { Badge, type BadgeVariant } from 'events-design-system'
import { getBookingStatusLabel, getBookingStatusVariant } from '../bookings/statuses.ts'

type Props = {
  status: string | null | undefined
}

export function StatusBadge({ status }: Props) {
  return <Badge variant={getBookingStatusVariant(status) as BadgeVariant}>{getBookingStatusLabel(status)}</Badge>
}
```

If `getBookingStatusVariant` can return a value outside `BadgeVariant` (e.g. `'neutral'` is already in the union; verify the returned strings are all members), keep the `as BadgeVariant` cast only if every returned value is a valid variant; otherwise map unknowns to `'neutral'` before passing.

- [ ] **Step 5: Refactor `UserInfo.tsx`** to render its resolved user through `UserInfoView` (keep all batch-loading logic; replace only the returned JSX). Replace the three inline `<span className="user-info…">` render branches with:

```tsx
import { UserInfoView } from 'events-design-system'
// … inside the component, once `user`/`loading` are resolved:
if (loading) return <UserInfoView name={null} email={fallback ?? ''} id={userId ?? undefined} variant={variant} loading />
if (!user) return <UserInfoView name={null} email={fallback ?? userId ?? ''} variant={variant} />
return <UserInfoView name={user.name} email={user.email} variant={variant} />
```

Preserve the component's existing prop names and the `userBatchLoader` wiring; only the JSX output changes.

- [ ] **Step 6: Typecheck, build, test**

Run: `cd event-admin-frontend && npm run build && npm test`
Expected: build + existing suite green. Fix any missed import path the build flags.

- [ ] **Step 7: Commit**

```bash
cd event-admin-frontend
git add -A
git commit -m "refactor: use events-design-system Icon/Switch/ErrorBoundary/Badge/UserInfoView"
```

---

### Task 6: Migrate `event-booker-frontend` (light — full sheet)

**Files:**
- Modify: `event-booker-frontend/package.json` (add `file:` dep)
- Modify: `event-booker-frontend/src/main.tsx` (import package styles)
- Modify: `event-booker-frontend/src/index.css` (reduce to only what the package doesn't provide)
- Modify: `event-booker-frontend/src/App.css` (drop bespoke primitives, keep layout)
- Delete: `event-booker-frontend/src/modules/shared/ErrorBoundary.tsx` (use package)
- Modify: `event-booker-frontend/src/main.tsx` (ErrorBoundary from package + `onError`)

**Interfaces:**
- Consumes: `events-design-system/styles.css` + `ErrorBoundary`.

- [ ] **Step 1: Add the dependency and install**

Add `"events-design-system": "file:../events-design-system"` to `event-booker-frontend/package.json` dependencies.
Run: `cd event-booker-frontend && npm install`

- [ ] **Step 2: Import the package stylesheet first in `main.tsx`**

Replace `import './index.css'` with:

```tsx
import 'events-design-system/styles.css'
import './index.css'
```

Keep `./index.css` for booker-only bits, but empty it of anything the package now supplies (Step 3).

- [ ] **Step 3: Trim `event-booker-frontend/src/index.css`** — the package now provides the reset, `body`, `button`, `a`, and font. Reduce `index.css` to only booker-specific globals not covered by the package (likely nothing left → leave the file with a single comment `/* booker-only globals: none; design system provides base */` to keep the import valid).

- [ ] **Step 4: Trim `event-booker-frontend/src/App.css`** — delete rules the package supplies (`.field`/`.field input` → use `.field` + `.field > input` from the package; `.inline-actions`, `.muted` → package; button styling → package `button`). KEEP app-specific layout: `.booker-shell`, `.booker-card`, `.event-type-card`, `.slot-grid`, `.slot-button`(+`.selected`), `.field-error`, `.banner-error`, `.spinner`/`@keyframes spin`. For `.booker-card`/`.event-type-card`, prefer reusing `.card` in markup where trivial; otherwise keep the local rule. Do not remove any selector still referenced by booker's JSX unless you also update the JSX to a package class.

- [ ] **Step 5: Replace booker's local ErrorBoundary with the package one**

```bash
cd event-booker-frontend && git rm src/modules/shared/ErrorBoundary.tsx
```

In `main.tsx`, replace the local import with `import { ErrorBoundary } from 'events-design-system'` and pass `onError`:

```tsx
import * as Sentry from '@sentry/react'
// …
<ErrorBoundary onError={(e, info) => Sentry.captureException(e, { extra: { componentStack: info.componentStack } })}>
```

- [ ] **Step 6: Build + test**

Run: `cd event-booker-frontend && npm run build && npm test`
Expected: build + existing vitest suite green.

- [ ] **Step 7: Manual smoke**

Run: `npm run dev` — walk the wizard (event-type list → slot picker → guest form → confirmation). Confirm the unified light brand (Plus Jakarta Sans, `--primary` buttons, `.card` surfaces) and that the slot grid / spinner still work.

- [ ] **Step 8: Commit (booker is root-tracked → commit in the ROOT `events` repo)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/package.json event-booker-frontend/package-lock.json event-booker-frontend/src
git commit -m "refactor(booker): adopt events-design-system light stylesheet + shared ErrorBoundary"
```

---

### Task 7: Migrate `jitsi-chat` (light — full sheet, dark theme dropped)

**Files:**
- Modify: `jitsi-chat/package.json` (add `file:` dep)
- Modify: `jitsi-chat/src/main.tsx` (import package styles)
- Modify: `jitsi-chat/src/layout.css` (recolour chrome to light tokens, keep layout, keep `.video-section` black)
- Modify: the file that sets the stream-chat theme (switch dark → light)

**Interfaces:**
- Consumes: `events-design-system/styles.css`.

- [ ] **Step 1: Add the dependency and install**

Add `"events-design-system": "file:../events-design-system"` to `jitsi-chat/package.json` dependencies.
Run: `cd jitsi-chat && npm install`

- [ ] **Step 2: Import the package stylesheet first in `main.tsx`**

Replace `import './index.css'` with:

```tsx
import 'events-design-system/styles.css'
import './index.css'
```

(`layout.css` is imported by `App.tsx` and loads after; that is fine.)

- [ ] **Step 3: Recolour `jitsi-chat/src/layout.css` to the light theme.** Change the `:root`/`body` dark globals and chrome colours to package tokens, keeping the layout geometry:
  - Remove `--bg-color: #1a1a1a` / `--text-color: #ffffff`; set `body,html,#root { background: var(--bg); color: var(--text); }` and drop the hard-coded `font-family` (the package sets Plus Jakarta Sans).
  - `.chat-section` → `background: var(--card); border-left: 1px solid var(--border); box-shadow: var(--shadow);`
  - `.stub-container`, `.chat-loading`, `.loading-spinner` → `color: var(--text); background: var(--bg);` (drop the dark `#1a1a1a`/white).
  - `.chat-toggle-btn` → `background: var(--card); color: var(--text-2); border: 1px solid var(--border);` and its `:hover` → `background: var(--bg);`.
  - `.toast-notification` → `background: var(--danger); color: #fff;` (keep — an error toast stays red).
  - `.video-section` → **keep** `background-color: #000` (video tiles are inherently dark).
  - Keep all geometry: `.app-container` flex, widths, `z-index`, responsive `@media`.
  - Keep `@import 'stream-chat-react/dist/css/v2/index.css';` at the top.

- [ ] **Step 4: Switch stream-chat to its light theme.** Find where the `Chat` component or its wrapper sets the theme class (grep for `str-chat__theme-dark` or a `theme=` prop in `jitsi-chat/src`). Change `str-chat__theme-dark` → `str-chat__theme-light` (or the equivalent `theme="str-chat__theme-light"`). If no explicit theme is set, add the light theme class to the chat container so the embedded chat matches.

Run: `cd jitsi-chat && grep -rn "str-chat__theme\|theme=" src`
Expected: locate and flip the theme.

- [ ] **Step 5: Build (+ tests if present)**

Run: `cd jitsi-chat && npm run build`
Expected: build succeeds. If jitsi has a test script, run `npm test`.

- [ ] **Step 6: Manual smoke**

Run: `npm run dev` — confirm the meeting layout is intact, the chat panel and toggle/toasts are now light and on-brand, the video area stays black, and stream-chat renders in light theme.

- [ ] **Step 7: Commit (in the jitsi-chat repo)**

```bash
cd jitsi-chat
git add package.json package-lock.json src/main.tsx src/layout.css
git add -A
git commit -m "refactor(jitsi): adopt events-design-system light theme; drop bespoke dark theme"
```

---

### Task 8: Release cutover — publish `events-design-system` and switch consumers to the git tag

This task requires GitHub access to create/push the package repo. If the repo cannot be created in-session, STOP and hand these steps to the human; the `file:` links from Tasks 4–7 keep every consumer working until then.

**Files:**
- Modify: `event-admin-frontend/package.json`, `event-booker-frontend/package.json`, `jitsi-chat/package.json` (swap `file:` → git tag)
- Create: `events-design-system/.github/workflows/ci.yml`

**Interfaces:**
- Produces: `events-design-system` `v0.1.0` tag consumable via `github:Lelikov/events-design-system#v0.1.0`.

- [ ] **Step 1: Add package CI `events-design-system/.github/workflows/ci.yml`**

```yaml
name: ci
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '22' }
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck
      - run: npm test
      - run: npm run build
```

Commit it in the package repo: `cd events-design-system && git add .github/workflows/ci.yml && git commit -m "ci: lint + typecheck + test + build"`.

- [ ] **Step 2: Create the GitHub repo and push**

```bash
cd events-design-system
gh repo create Lelikov/events-design-system --private --source=. --push
```

Expected: repo created, `main` pushed. (If `gh` is unavailable, create the repo in the UI and `git remote add origin … && git push -u origin main`.)

- [ ] **Step 3: Tag `v0.1.0` and push the tag**

```bash
cd events-design-system
git tag v0.1.0
git push origin v0.1.0
```

- [ ] **Step 4: Switch each consumer from `file:` to the git tag**

In `event-admin-frontend/package.json`, `event-booker-frontend/package.json`, `jitsi-chat/package.json`, change the dependency to:

```json
"events-design-system": "github:Lelikov/events-design-system#v0.1.0"
```

Then in each: `npm install` (regenerates the lockfile against the tag), `npm run build && npm test` (admin/booker) or `npm run build` (jitsi). Expected: all green resolving the tagged package.

- [ ] **Step 5: Commit the cutover in each repo**

- admin: `cd event-admin-frontend && git add package.json package-lock.json && git commit -m "chore: pin events-design-system v0.1.0"`
- jitsi: `cd jitsi-chat && git add package.json package-lock.json && git commit -m "chore: pin events-design-system v0.1.0"`
- booker (root repo): `cd /Users/alexandrlelikov/PycharmProjects/events && git add event-booker-frontend/package.json event-booker-frontend/package-lock.json && git commit -m "chore(booker): pin events-design-system v0.1.0"`

- [ ] **Step 6: Update docs**

Add a row for `events-design-system` to the root `CLAUDE.md` service table (library, no runtime; consumed by the three SPAs) and a one-line note in `docs/architecture/ONBOARDING.md` that the SPAs share `events-design-system` (git-tag, `file:` link for local dev, mirrors `event-schemas`). Commit in the root `events` repo.

---

## Notes for the executor

- **Local-dev linking:** `file:../events-design-system` requires the package's `prepare` (tsup) to succeed on install, so Task 1 must be green before any consumer task. If a consumer's `npm install` doesn't rebuild after a package change, run `npm run build` inside `events-design-system` (or `npm rebuild events-design-system` in the consumer).
- **Task order dependency:** Tasks 1→2→3 build the package; 4→5 migrate admin; 6 booker; 7 jitsi; 8 cutover. 4 must precede 5 (5 deletes local components the CSS task doesn't touch). 6/7 are independent of 4/5 but all depend on 1–3.
- **Fidelity check for admin (Task 4):** if anything looks off, diff the concatenation of `styles.css` (minus the `@import` line) + `app.css` against the original `index.css` (minus the two `:root` blocks) — they must contain the same rule bodies.
