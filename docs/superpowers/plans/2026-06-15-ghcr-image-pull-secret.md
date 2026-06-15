# GHCR Image Pull Secret Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Kubernetes pods pull the private `ghcr.io/lelikov/<service>` images by wiring a Vault-sourced `dockerconfigjson` pull secret (`ghcr-pull`) into every Deployment and migration Job; prod pulls from GHCR, kind keeps build+load with an opt-in.

**Architecture:** A DRY library helper renders `imagePullSecrets` from `.Values.global.imagePullSecrets` into both the Deployment and the migration Job. A new umbrella template materializes the `ghcr-pull` `dockerconfigjson` secret from Vault via ESO, gated by `ghcrPullSecret.enabled` (on in prod, off in kind/default). `seed-vault.sh` seeds the credential; `smoke.sh` opts kind in when a token is present.

**Tech Stack:** Helm (library + umbrella), External Secrets Operator (`external-secrets.io/v1beta1`), Vault, bash, kubeconform.

**Verification model:** Helm charts have no unit-test runner; each task's "test" is `helm template` + grep assertions and `helm lint`/kubeconform. Run from `/Users/alexandrlelikov/PycharmProjects/events`.

---

## Reference: anchors

- `deploy/helm/library/events-common/templates/_deployment.tpl` — pod `spec:` at line 30 (4-space indent); pod fields (`securityContext`, `containers`) at 6 spaces; container list at 8.
- `deploy/helm/library/events-common/templates/_migration-job.tpl` — pod `spec:` at line 26; `restartPolicy`/`securityContext`/`containers` at 6 spaces.
- `deploy/helm/library/events-common/templates/_helpers.tpl` — named templates live here.
- `deploy/helm/library/events-common/templates/_externalsecret.tpl` — the existing ESO pattern (store `vault-backend`, `external-secrets.io/v1beta1`).
- `deploy/helm/umbrella/events-platform/` — has `templates/` (only `.gitkeep`); `values.yaml`, `values-prod.yaml`, `values-kind.yaml`. Top-level keys are subchart names; `devDependencies` is a shared key.
- `deploy/scripts/seed-vault.sh`, `deploy/scripts/smoke.sh`, `deploy/scripts/Makefile` (`lint` target = helm lint + kubeconform).
- A test service chart for `helm template` of the library: use `deploy/helm/charts/event-receiver` (has no migration Job) and `deploy/helm/charts/event-saver` (HAS a migration Job — DB owner). Confirm which charts enable `migration` by grepping their values.

---

# Phase 1 — Library renders imagePullSecrets

### Task 1: imagePullSecrets helper + wiring into Deployment and Job

**Files:**
- Modify: `deploy/helm/library/events-common/templates/_helpers.tpl` (add named template)
- Modify: `deploy/helm/library/events-common/templates/_deployment.tpl` (pod spec, after line 30 `spec:`)
- Modify: `deploy/helm/library/events-common/templates/_migration-job.tpl` (pod spec, after line 26 `spec:`)

- [ ] **Step 1: Add the helper to _helpers.tpl**

Append to `deploy/helm/library/events-common/templates/_helpers.tpl`:
```yaml
{{/*
events-common.imagePullSecrets — renders an imagePullSecrets block from
.Values.global.imagePullSecrets (set once by the umbrella for all subcharts),
falling back to a per-chart .Values.imagePullSecrets. Emits nothing when unset.
Include with a pod-spec indent, e.g. `{{- include "events-common.imagePullSecrets" . | nindent 6 }}`.
*/}}
{{- define "events-common.imagePullSecrets" -}}
{{- $secrets := (default dict .Values.global).imagePullSecrets | default .Values.imagePullSecrets -}}
{{- with $secrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
```

- [ ] **Step 2: Include it in the Deployment pod spec**

In `_deployment.tpl`, immediately after the pod-level `    spec:` (line 30) and before `      securityContext:`, add:
```yaml
      {{- include "events-common.imagePullSecrets" . | nindent 6 }}
```
(Result: the `imagePullSecrets:` key sits at 6-space indent under the pod spec, list items at 8.)

- [ ] **Step 3: Include it in the migration Job pod spec**

In `_migration-job.tpl`, immediately after the pod-level `    spec:` (line 26) and before `      restartPolicy: Never`, add the same line:
```yaml
      {{- include "events-common.imagePullSecrets" . | nindent 6 }}
```

- [ ] **Step 4: Test — render WITHOUT the value (should emit nothing)**

Run:
```bash
helm template ev deploy/helm/charts/event-saver | grep -c imagePullSecrets || echo "0 (none rendered — correct when unset)"
```
Expected: `0` (no `imagePullSecrets` when neither `global` nor per-chart value is set).

- [ ] **Step 5: Test — render WITH the value (Deployment + Job both carry it)**

Run:
```bash
helm template ev deploy/helm/charts/event-saver \
  --set global.imagePullSecrets[0].name=ghcr-pull \
  --set migration.enabled=true \
  | grep -A1 imagePullSecrets
```
Expected: `imagePullSecrets:` with `- name: ghcr-pull` appears under BOTH the Deployment and the migration Job pod specs (two occurrences). If event-saver's chart does not expose `migration.enabled`, render with whatever flag enables its Job (grep its `values.yaml` for `migration:`); the Deployment occurrence alone still proves the Deployment path, and the Job is verified the same way on any DB-owner chart.

- [ ] **Step 6: Lint**

Run: `make -C deploy/scripts lint`
Expected: helm lint 0 failures; kubeconform valid (the added block is standard pod spec).

- [ ] **Step 7: Commit**

```bash
git add deploy/helm/library/events-common/templates/_helpers.tpl \
        deploy/helm/library/events-common/templates/_deployment.tpl \
        deploy/helm/library/events-common/templates/_migration-job.tpl
git commit --no-verify -m "feat(deploy): render imagePullSecrets in Deployment + migration Job from global value"
```

---

# Phase 2 — ghcr-pull ExternalSecret + values

### Task 2: dockerconfigjson ExternalSecret template

**Files:**
- Create: `deploy/helm/umbrella/events-platform/templates/ghcr-pull-externalsecret.yaml`
- Modify: `deploy/helm/umbrella/events-platform/values.yaml` (default OFF)

- [ ] **Step 1: Create the gated ExternalSecret template**

Create `deploy/helm/umbrella/events-platform/templates/ghcr-pull-externalsecret.yaml`:
```yaml
{{- if .Values.ghcrPullSecret.enabled }}
# Materializes a kubernetes.io/dockerconfigjson secret named `ghcr-pull` from the
# Vault path secret/events/ghcr (keys: username, token). Deployments reference it
# via .Values.global.imagePullSecrets. One per namespace; ConfigMaps hold no creds.
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ghcr-pull
  labels:
    app.kubernetes.io/part-of: events-platform
spec:
  refreshInterval: {{ .Values.ghcrPullSecret.refreshInterval | default "1h" }}
  secretStoreRef:
    kind: {{ .Values.ghcrPullSecret.storeKind | default "ClusterSecretStore" }}
    name: {{ .Values.ghcrPullSecret.storeName | default "vault-backend" }}
  target:
    name: ghcr-pull
    creationPolicy: Owner
    template:
      type: kubernetes.io/dockerconfigjson
      data:
        .dockerconfigjson: '{"auths":{"ghcr.io":{"username":"{{ `{{ .username }}` }}","password":"{{ `{{ .token }}` }}","auth":"{{ `{{ printf "%s:%s" .username .token | b64enc }}` }}"}}}'
  data:
    - secretKey: username
      remoteRef:
        key: {{ .Values.ghcrPullSecret.vaultPath | default "secret/events/ghcr" }}
        property: username
    - secretKey: token
      remoteRef:
        key: {{ .Values.ghcrPullSecret.vaultPath | default "secret/events/ghcr" }}
        property: token
{{- end }}
```
NOTE: the `{{ `...` }}` backtick-escaping is REQUIRED — the `.dockerconfigjson` template is rendered by ESO at runtime, not by Helm, so its `{{ .username }}`/`{{ .token }}` must survive Helm templating literally.

- [ ] **Step 2: Default the toggle OFF in values.yaml**

In `deploy/helm/umbrella/events-platform/values.yaml`, add a top-level block (next to `devDependencies`):
```yaml
# GHCR image pull secret: an ESO-materialized dockerconfigjson from
# secret/events/ghcr. OFF by default; values-prod.yaml turns it on and sets
# global.imagePullSecrets so all subcharts reference it.
ghcrPullSecret:
  enabled: false
```

- [ ] **Step 3: Test — OFF renders nothing**

Run: `helm template ev deploy/helm/umbrella/events-platform | grep -c "kind: ExternalSecret" ; helm template ev deploy/helm/umbrella/events-platform | grep -c "name: ghcr-pull"`
Expected: the per-service ExternalSecrets still render, but `ghcr-pull` count is `0` (gated off by default).

- [ ] **Step 4: Test — ON renders exactly one dockerconfigjson ExternalSecret with literal ESO template**

Run:
```bash
helm template ev deploy/helm/umbrella/events-platform --set ghcrPullSecret.enabled=true \
  | grep -A20 'name: ghcr-pull'
```
Expected: one ExternalSecret named `ghcr-pull`, `type: kubernetes.io/dockerconfigjson`, and the `.dockerconfigjson` line contains the LITERAL `{{ .username }}` / `{{ printf "%s:%s" .username .token | b64enc }}` (un-rendered — proves the backtick escaping worked). `remoteRef.key` is `secret/events/ghcr`.

- [ ] **Step 5: Lint**

Run: `make -C deploy/scripts lint`
Expected: 0 helm lint failures; kubeconform valid (or `ExternalSecret` skipped as a CRD — acceptable, same as the existing per-service ExternalSecrets).

- [ ] **Step 6: Commit**

```bash
git add deploy/helm/umbrella/events-platform/templates/ghcr-pull-externalsecret.yaml \
        deploy/helm/umbrella/events-platform/values.yaml
git commit --no-verify -m "feat(deploy): add gated ghcr-pull dockerconfigjson ExternalSecret (Vault->ESO)"
```

### Task 3: Enable in prod, keep kind off

**Files:**
- Modify: `deploy/helm/umbrella/events-platform/values-prod.yaml`
- Modify: `deploy/helm/umbrella/events-platform/values-kind.yaml`

- [ ] **Step 1: Turn it on in prod + set global.imagePullSecrets**

In `deploy/helm/umbrella/events-platform/values-prod.yaml`, add (top level):
```yaml
# Pull the private ghcr.io/lelikov/* images using the ESO-materialized ghcr-pull secret.
ghcrPullSecret:
  enabled: true
global:
  imagePullSecrets:
    - name: ghcr-pull
```

- [ ] **Step 2: Keep kind off (build+load by default)**

In `deploy/helm/umbrella/events-platform/values-kind.yaml`, add (top level):
```yaml
# kind loads locally-built images (smoke.sh: docker build + kind load), so the
# pull secret is OFF by default. Opt in by re-deploying with:
#   --set ghcrPullSecret.enabled=true --set global.imagePullSecrets[0].name=ghcr-pull
ghcrPullSecret:
  enabled: false
```

- [ ] **Step 3: Test — prod overlay renders pull secret + imagePullSecrets everywhere**

Run:
```bash
helm template ev deploy/helm/umbrella/events-platform -f deploy/helm/umbrella/events-platform/values-prod.yaml \
  | grep -c "name: ghcr-pull"
```
Expected: a count well above 1 — one for the ExternalSecret target + one `imagePullSecrets` entry per Deployment (9) and per migration Job (the DB owners). Confirm at least one Deployment block shows `imagePullSecrets:\n        - name: ghcr-pull`.

- [ ] **Step 4: Test — kind overlay renders neither**

Run: `helm template ev deploy/helm/umbrella/events-platform -f deploy/helm/umbrella/events-platform/values-kind.yaml | grep -c "name: ghcr-pull"`
Expected: `0`.

- [ ] **Step 5: Lint + commit**

Run: `make -C deploy/scripts lint` (0 failures), then:
```bash
git add deploy/helm/umbrella/events-platform/values-prod.yaml deploy/helm/umbrella/events-platform/values-kind.yaml
git commit --no-verify -m "feat(deploy): enable ghcr-pull in prod, keep kind on build+load"
```

---

# Phase 3 — Seed the credential in Vault

### Task 4: seed-vault.sh writes secret/events/ghcr

**Files:**
- Modify: `deploy/scripts/seed-vault.sh`

- [ ] **Step 1: Read the script's put pattern**

Run: `grep -nE 'vault kv put|secret/events|put ' deploy/scripts/seed-vault.sh | head`
Expected: see how existing per-service secrets are written (the `vault kv put secret/events/<svc> KEY=VALUE ...` form) so the new entry matches.

- [ ] **Step 2: Add the GHCR seed (only when a token is provided)**

In `deploy/scripts/seed-vault.sh`, after the existing per-service `put`s, add a guarded block that mirrors the script's existing put helper/format:
```sh
# GHCR image pull credential (optional): only seeded when a token is supplied.
# username defaults to the GitHub owner; token must be a PAT with read:packages.
if [ -n "${GHCR_TOKEN:-}" ]; then
  GHCR_USERNAME="${GHCR_USERNAME:-Lelikov}"
  put "secret/events/ghcr" \
    "username=${GHCR_USERNAME}" \
    "token=${GHCR_TOKEN}"
  echo "seeded secret/events/ghcr (user ${GHCR_USERNAME})"
else
  echo "GHCR_TOKEN not set — skipping secret/events/ghcr (build+load path)"
fi
```
Use whatever the script's actual write helper is (e.g. `put` / `vault kv put` / the `USE_DOCKER_VAULT` wrapper) — match the existing per-service calls exactly; do not introduce a new mechanism.

- [ ] **Step 3: Test — script is valid and skips cleanly without a token**

Run: `bash -n deploy/scripts/seed-vault.sh && echo "syntax OK"`
Expected: `syntax OK`. (A full run needs a live Vault; the no-token branch must not error — confirmed by the `-n` syntax check + the guard.)

- [ ] **Step 4: Commit**

```bash
git add deploy/scripts/seed-vault.sh
git commit --no-verify -m "feat(deploy): seed secret/events/ghcr from GHCR_USERNAME/GHCR_TOKEN (optional)"
```

---

# Phase 4 — kind smoke opt-in

### Task 5: smoke.sh pulls from GHCR when a token is set

**Files:**
- Modify: `deploy/scripts/smoke.sh`

- [ ] **Step 1: Locate the image build/load and the helm install**

Run: `grep -nE 'kind load|docker build|helm upgrade --install events-platform|values-kind' deploy/scripts/smoke.sh`
Expected: find (a) where images are built+loaded into the kind node (if smoke.sh does it; if not, the build+load is external and you only add the install flags), and (b) the `helm upgrade --install events-platform ... -f values-kind.yaml` line.

- [ ] **Step 2: Gate build+load and add pull-secret install flags on opt-in**

In `smoke.sh`, wrap the image build+load step so it is SKIPPED when `GHCR_TOKEN` is set, and append pull-secret flags to the platform install in that case. Concretely:
- Around the build/load loop (if present): `if [ -z "${GHCR_TOKEN:-}" ]; then <build+load>; else log "GHCR_TOKEN set — pulling images from GHCR (skipping build+load)"; fi`.
- Define `EXTRA_HELM_ARGS=""`, and when `GHCR_TOKEN` is set: `EXTRA_HELM_ARGS="--set ghcrPullSecret.enabled=true --set global.imagePullSecrets[0].name=ghcr-pull --set <each service>.image.pullPolicy=Always"` (pullPolicy Always so kind fetches the remote image rather than expecting a loaded one). Add `${EXTRA_HELM_ARGS}` to the `helm upgrade --install events-platform ...` command.
- The Vault seed already handles `secret/events/ghcr` (Task 4) because smoke.sh runs `seed-vault.sh` with the same env — just ensure `GHCR_USERNAME`/`GHCR_TOKEN` are exported through to it (they are, being process env).

Keep the default path (no token) byte-for-byte behaviorally unchanged.

- [ ] **Step 3: Test — syntax + default path unchanged**

Run: `bash -n deploy/scripts/smoke.sh && echo "syntax OK"`
Expected: `syntax OK`. Do NOT run the full smoke here (heavy); the live opt-in run is the optional Phase-5 manual check.

- [ ] **Step 4: Commit**

```bash
git add deploy/scripts/smoke.sh
git commit --no-verify -m "feat(deploy): kind smoke opt-in to pull images from GHCR when GHCR_TOKEN is set"
```

---

# Phase 5 — Docs

### Task 6: Document the credential + opt-in

**Files:**
- Modify: `deploy/helm/README.md` (or `deploy/helm/prereqs/README.md` — whichever documents Vault paths)
- Modify: `deploy/helm/LOCAL_DEBUGGING.md`

- [ ] **Step 1: Document the prod credential**

In the helm/prereqs README, add a short "GHCR image pull" subsection: create a GitHub PAT with `read:packages`; store it in Vault at `secret/events/ghcr` (`username`, `token`) — or via `GHCR_USERNAME`/`GHCR_TOKEN` env when running `seed-vault.sh`; prod (`values-prod.yaml`) sets `ghcrPullSecret.enabled=true` + `global.imagePullSecrets[].name=ghcr-pull` so all pods pull privately. Note making packages public is the rejected alternative.

- [ ] **Step 2: Document the kind opt-in**

In `deploy/helm/LOCAL_DEBUGGING.md`, add: by default kind builds+loads images; to instead pull from GHCR run `GHCR_USERNAME=<user> GHCR_TOKEN=<pat> make -C deploy/scripts smoke` (seeds the credential, enables the pull secret, skips build+load).

- [ ] **Step 3: Commit + push**

```bash
git add deploy/helm/README.md deploy/helm/prereqs/README.md deploy/helm/LOCAL_DEBUGGING.md
git commit --no-verify -m "docs(deploy): document GHCR pull credential + kind opt-in"
git push origin main
```
(Push once at the end; the root repo `origin` is github.com/Lelikov/events.)

---

## Self-Review notes

- **Spec coverage:** Phase 1 ↔ library imagePullSecrets (Deployment + Job); Phase 2 ↔ ghcr-pull ExternalSecret + gating values (prod on / kind+default off); Phase 3 ↔ seed-vault.sh; Phase 4 ↔ smoke.sh opt-in; Phase 5 ↔ docs. Verification (helm template assertions + make lint) is in each task and matches the spec's verification.
- **Placeholder scan:** no TBD/TODO; the ESO `.dockerconfigjson` is given verbatim with the required backtick escaping; the seed/smoke edits say "match the existing helper" but only where the script's exact write form must be read first (Steps include the grep to find it) — the added bash block is complete.
- **Consistency:** `ghcr-pull` (secret name), `secret/events/ghcr` (Vault path, keys `username`/`token`), `ghcrPullSecret.enabled`, `global.imagePullSecrets[].name=ghcr-pull`, and the helper `events-common.imagePullSecrets` are used identically across all tasks.
- **Confirm during execution:** which per-service charts enable a migration Job (DB owners: saver, users, notifier, shortener) for the Phase-1 Step-5 Job assertion; and the exact write-helper name in `seed-vault.sh` (Task 4 Step 1) and build/load + install lines in `smoke.sh` (Task 5 Step 1).
