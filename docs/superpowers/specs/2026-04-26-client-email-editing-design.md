# Client Email Editing — Design Spec

## Problem

Admins need to change a client's email address from the admin frontend. Changes must:
- Be reflected in event-users (source of truth for user data)
- Be delivered to the external CRM via webhook
- Not be overwritten by periodic CRM sync before the webhook reaches the CRM
- Maintain a full audit trail (who changed, when, from what to what)

## Data Flow

```
event-admin-frontend (modal form)
  → event-admin (pre-validation + publish CloudEvent)
    → event-receiver (routes to queue)
      → RabbitMQ [events.user.email]
        → event-users (consumer: update email, changelog, webhook outbox)
          → CRM (webhook with retries via outbox poller)
```

Read path for changelog:
```
event-admin-frontend → event-admin (proxy) → event-users GET /api/users/{id}/email-changelog
```

## Changes Per Service

### 1. event-schemas

**New event type:**
- `USER_EMAIL_CHANGE_REQUESTED` = `"user.email.change_requested"`
- Priority: `10` (CRITICAL) — admin action, must not be delayed
- Schema version: `"v1"`

**New payload model** (`UserEmailChangeRequestedPayload`):
```python
class UserEmailChangeRequestedPayload(BaseModel):
    user_id: str          # UUID of the client user
    old_email: EmailStr
    new_email: EmailStr
    requested_by: str     # admin email from JWT sub
```

### 2. event-receiver

**New ingest endpoint:**
- `POST /event/admin` — accepts CloudEvents from event-admin
- Auth: static API key (same pattern as `POST /event/booking`)
- Validates payload against `UserEmailChangeRequestedPayload` schema
- Publishes to RabbitMQ topic exchange

**New routing rule:**
- Source pattern: `admin`
- Type pattern: `user.email.*`
- Destination queue: `events.user.email`

**New queue in topology:**
- `events.user.email` — durable, priority-enabled, DLQ-bound (standard pattern)

### 3. event-admin

**New endpoint: `POST /api/users/{user_id}/change-email`**
- Auth: JWT with `role=admin`
- Request body:
  ```json
  { "new_email": "new@example.com" }
  ```
- Flow:
  1. Extract `admin_email` from JWT `sub` claim
  2. Fetch current user from event-users (`GET /api/users/id/{user_id}`) — get `old_email`, verify user exists, verify `role=client`
  3. Pre-validate uniqueness: call `GET /api/users/roles/client/emails/{new_email}` on event-users — expect 404 (available) or return 409 (conflict)
  4. Publish CloudEvent to event-receiver `POST /event/admin`:
     - `type`: `user.email.change_requested`
     - `source`: `admin`
     - `data`: `{ user_id, old_email, new_email, requested_by: admin_email }`
  5. Return `202 Accepted`
- Errors: `404` (user not found), `409` (email taken), `422` (validation), `502` (event-receiver unavailable)

**New endpoint: `GET /api/users/{user_id}/email-changelog`**
- Auth: JWT with `role=admin`
- Proxies to event-users `GET /api/users/{user_id}/email-changelog`
- Returns list of changelog entries

**New dependencies:**
- `IEventPublisher` protocol + `EventPublisherClient` adapter (httpx POST to event-receiver)
- Config: `EVENT_RECEIVER_URL`, `EVENT_RECEIVER_API_TOKEN`

### 4. event-users

#### 4.1 Database Changes

**New column on `users` table:**
```sql
ALTER TABLE users ADD COLUMN email_source TEXT NOT NULL DEFAULT 'crm';
-- Values: 'crm', 'admin'
```

**New table `user_email_changelog`:**
```sql
CREATE TABLE user_email_changelog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    old_email TEXT NOT NULL,
    new_email TEXT NOT NULL,
    changed_by TEXT NOT NULL,       -- admin email
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_user_email_changelog_user_id ON user_email_changelog(user_id);
CREATE INDEX ix_user_email_changelog_changed_at ON user_email_changelog(changed_at DESC);
```

**New table `webhook_outbox`:**
```sql
CREATE TABLE webhook_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,           -- e.g. 'user.email.changed'
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, processing, delivered, failed
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 5,
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    last_error TEXT
);
CREATE INDEX ix_webhook_outbox_status_next_retry ON webhook_outbox(status, next_retry_at)
    WHERE status IN ('pending', 'processing');
```

#### 4.2 RabbitMQ Consumer

**New FastStream consumer** subscribed to `events.user.email` queue:
- Parses CloudEvent binary mode (same pattern as event-saver consumer)
- Handles `user.email.change_requested`:
  1. Update `users.email` WHERE `id = user_id`
  2. Set `users.email_source = 'admin'`
  3. Upsert `user_contacts` record for `channel='email'` with new email
  4. Insert into `user_email_changelog` (user_id, old_email, new_email, changed_by, changed_at)
  5. Insert into `webhook_outbox` (event_type='user.email.changed', payload with user_id, old_email, new_email)
  6. All in one transaction

#### 4.3 Webhook Outbox Poller

**Background task** (same pattern as event-notifier `OutboxSender`):
- Polls `webhook_outbox` every 1 second
- `SELECT ... WHERE status IN ('pending', 'processing') AND next_retry_at <= now() FOR UPDATE SKIP LOCKED LIMIT 10`
- Per record:
  - Set `status = 'processing'`
  - HTTP POST to configurable `CRM_WEBHOOK_URL` with payload:
    ```json
    {
      "event_type": "user.email.changed",
      "user_id": "<uuid>",
      "old_email": "old@example.com",
      "new_email": "new@example.com",
      "changed_at": "2026-04-26T12:00:00Z"
    }
    ```
  - On success: `status = 'delivered'`, `delivered_at = now()`, reset `email_source = 'crm'` on users table
  - On failure: increment `attempts`, set `next_retry_at` with exponential backoff (`10 * attempts^2` seconds), set `last_error`
  - After `max_attempts`: `status = 'failed'` (manual intervention needed)
- Auth: configurable Bearer token or HMAC signature header

#### 4.4 CRM Sync Protection

Modify `upsert_user_from_crm()` SQL:
```sql
INSERT INTO users (email, name, role, time_zone, email_source)
VALUES (:email, :name, :role, :time_zone, 'crm')
ON CONFLICT (email, role)
DO UPDATE SET
    name = COALESCE(EXCLUDED.name, users.name),
    time_zone = COALESCE(EXCLUDED.time_zone, users.time_zone),
    updated_at = now()
WHERE users.email_source != 'admin';
```

The `WHERE users.email_source != 'admin'` clause prevents CRM from overwriting admin-set email. Since the conflict is on `(email, role)` and the email itself changed, the CRM row with the old email will either:
- Match the old `(email, role)` pair and be blocked by the WHERE clause
- Not conflict at all (new email not in CRM yet) — which is fine, no overwrite

**Edge case — duplicate creation**: CRM sync sends the *old* email. After admin changed email from `old@mail.com` to `new@mail.com`, the DB row now has `(new@mail.com, client)`. CRM sends `(old@mail.com, client)` — no conflict found — INSERT creates a duplicate user with the old email.

**Solution**: Two-layer protection in `upsert_user_from_crm()`:

1. **Before upsert**: query `user_email_changelog` to check if this email was recently changed away from:
   ```python
   # Check if CRM is trying to insert an email that was changed by admin
   changelog_row = await sql.fetch_one(
       """SELECT user_id FROM user_email_changelog
          WHERE old_email = :email AND user_id IN (
              SELECT id FROM users WHERE role = :role AND email_source = 'admin'
          )
          ORDER BY changed_at DESC LIMIT 1""",
       {"email": email, "role": role},
   )
   if changelog_row:
       logger.info("Skipping CRM upsert: email was changed by admin", email=email, role=role)
       return  # skip this user entirely
   ```

2. **In the upsert SQL itself**: add WHERE clause to prevent overwriting admin-set data:
   ```sql
   ON CONFLICT (email, role)
   DO UPDATE SET
       name = COALESCE(EXCLUDED.name, users.name),
       time_zone = COALESCE(EXCLUDED.time_zone, users.time_zone),
       updated_at = now()
   WHERE users.email_source != 'admin'
   ```

Both layers are needed: layer 1 prevents duplicate creation with old email, layer 2 prevents overwriting if CRM somehow matches the existing row.

#### 4.5 New REST Endpoint

**`GET /api/users/{user_id}/email-changelog`**
- Auth: Bearer token (same as existing endpoints)
- Query params: `limit` (default 20), `offset` (default 0)
- Response:
  ```json
  {
    "items": [
      {
        "id": "<uuid>",
        "old_email": "old@example.com",
        "new_email": "new@example.com",
        "changed_by": "admin@company.com",
        "changed_at": "2026-04-26T12:00:00Z"
      }
    ],
    "total": 5
  }
  ```

### 5. event-admin-frontend

**Email Edit Modal** — reusable component used in two places:

**Trigger points:**
- BookingDetailsPage: edit icon button next to client's email in "Current participants" card
- ParticipantsPage: edit icon button in the email column for rows with `role=client`

**Modal content:**
- Header: "Change client email"
- Current email (read-only display)
- New email input field with validation (format + not same as current)
- Changelog table below the form (loaded from `GET /api/users/{user_id}/email-changelog`)
- "Save" button → calls `POST /api/users/{user_id}/change-email`
- On success: show confirmation message, close modal, invalidate user cache
- On error: display error inline (409 = "Email already in use", 404 = "User not found")

**Note:** Since the flow is async (event-admin returns 202, actual update happens via RabbitMQ), the modal shows a success message "Change request submitted" rather than "Email changed". The user list will update after event-users processes the event and cache invalidation fires.

## CRM Overwrite Protection — Full Scenario

```
1. Admin changes client email: old@mail.com → new@mail.com
2. event-users updates:
   - users.email = 'new@mail.com'
   - users.email_source = 'admin'
   - user_email_changelog += record
   - webhook_outbox += record (status=pending)
3. CRM sync runs (every 5 min):
   - Fetches users from CRM, finds old@mail.com for role=client
   - Before upsert: checks user_email_changelog — finds old@mail.com was changed away by admin for a user with email_source='admin' → skips this CRM user entirely
   - No duplicate created, no overwrite
4. Webhook outbox delivers to CRM:
   - POST to CRM with {old_email, new_email}
   - CRM updates its records
   - On success: users.email_source = 'crm'
5. Next CRM sync:
   - CRM now has new@mail.com
   - Upsert matches, email_source='crm', no conflict
```

## Out of Scope

- Editing organizer email (only client email)
- Editing other user fields (name, timezone) through this flow
- Batch email changes
- Email change notifications to the client themselves
- Real-time UI update (websocket) after async processing — relies on cache invalidation + manual refresh
