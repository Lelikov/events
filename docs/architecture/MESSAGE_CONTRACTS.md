# Message Contracts

## Overview

All inter-service messages use the **CloudEvents specification v1.0** in **binary content mode**:

- **Transport:** AMQP 0.9.1 via RabbitMQ
- **Exchange:** `events` (topic, durable)
- **Routing:** Routing key = queue name; first-match glob pattern rules in event-receiver
- **Headers:** `ce-type`, `ce-source`, `ce-id`, `ce-time`, `ce-booking_id`, `ce-specversion`, `ce-idempotencykey`, `ce-traceid`, `ce-spanid`, `ce-dataschema`
- **Body:** JSON event payload wrapped in `{"original": {...}, "normalized": {"participants": [...]}}`

The `original` key contains the raw source payload unchanged. The `normalized` key contains enriched participant data (with `user_id` UUIDs resolved from event-users). All consumers MUST read source-specific fields from `original`, not from the top-level body.

**Source:** `event-receiver/event_receiver/adapters/publisher.py:89-93`, `event-receiver/event_receiver/normalizers.py:53`

Priority is set via AMQP `priority` property (0-10 scale) using the `EVENT_PRIORITIES` map from event-schemas (`event-schemas/event_schemas/types.py:87-116`).

**Source:** `event-receiver/event_receiver/adapters/publisher.py:67-71`, `event-saver/event_saver/adapters/consumer.py:51-68`

---

## Exchange and Queue Registry

Reformatted from `docs/audit/CONTRACT_MAP.md`.

### Exchanges

| Exchange | Type | Durable | Declared by | Purpose |
|----------|------|---------|-------------|---------|
| `events` | topic | yes | event-receiver (`RabbitTopologyManager.ensure_topology`), event-saver (`RabbitTopologyManager.ensure_topology`) | Primary message exchange for all CloudEvents |
| `events.dlx` | topic | yes | event-receiver only | Dead-letter exchange for failed messages |

**Note:** event-saver's topology manager does NOT create the DLX exchange or DLQ queues. event-notifier does not call `ensure_topology` at all -- it subscribes with `declare=False`.

### Queues

All main queues bind to the `events` exchange with routing key = queue name.

| Queue | Consumer | DLQ? | Priority? | Purpose |
|-------|----------|------|-----------|---------|
| `events.booking.lifecycle` | event-saver | yes (receiver) / no (saver) | 10 | Booking created/cancelled/reassigned/rescheduled |
| `events.booking.reminder` | event-saver | yes / no | 10 | Booking reminder events |
| `events.chat.lifecycle` | event-saver | yes / no | 10 | Chat created/deleted |
| `events.chat.activity` | event-saver | yes / no | 10 | Chat messages |
| `events.meeting.lifecycle` | event-saver | yes / no | 10 | Meeting URL created/deleted |
| `events.notification.delivery` | event-saver | yes / no | 10 | Notification delivery confirmations |
| `events.notification.commands` | event-notifier (intended) | yes / no | 10 | Notification send commands |
| `events.notifications` | none (orphaned) | yes / no | 10 | Phantom queue, previously event-notifier's incorrect default; no longer consumed |
| `events.jitsi` | event-saver | yes / no | 10 | Jitsi meeting events |
| `events.mail` | event-saver | yes / no | 10 | UniSender status callbacks |
| `events.chat` | event-saver | yes / no | 10 | GetStream webhook events |
| `events.user.email` | event-users | yes / no | 10 | Email change requests |
| `events.unrouted` | event-saver (fallback) | yes / no | 10 | Unmatched events |
| `*.dlq` variants | none (dead-letter storage) | -- | -- | 24h TTL dead-letter storage |

**Inconsistency:** event-receiver creates queues with `x-max-priority=10` and `x-dead-letter-exchange=events.dlx`. event-saver creates plain durable queues without those arguments. If both declare the same queue with different arguments, RabbitMQ rejects the second declaration (audit IC-4).

**Source:** `docs/audit/CONTRACT_MAP.md:22-40`

---

## Complete Message Type Registry

| CloudEvent `type` | Producer | Consumer | Routing Key (actual) | Priority | Payload Schema |
|-------------------|----------|----------|---------------------|----------|----------------|
| `booking.created` | event-receiver | event-notifier (via `events.notifications`) | `events.notifications` | 10 (CRITICAL) | `BookingCreatedPayload` |
| `booking.rescheduled` | event-receiver | event-notifier | `events.notifications` | 10 (CRITICAL) | `BookingRescheduledPayload` |
| `booking.reassigned` | event-receiver | event-notifier | `events.notifications` | 10 (CRITICAL) | `BookingReassignedPayload` |
| `booking.cancelled` | event-receiver | event-notifier | `events.notifications` | 10 (CRITICAL) | `BookingCancelledPayload` |
| `booking.reminder_sent` | event-receiver | event-notifier | `events.notifications` | 7 (HIGH) | `BookingReminderSentPayload` |
| `chat.created` | event-receiver | event-saver | `events.chat.lifecycle` | 5 (NORMAL) | `ChatCreatedPayload` |
| `chat.deleted` | event-receiver | event-saver | `events.chat.lifecycle` | 5 (NORMAL) | `ChatDeletedPayload` |
| `chat.message_sent` | event-receiver | event-saver | `events.chat.activity` | 5 (NORMAL) | `ChatMessageSentPayload` |
| `meeting.url_created` | event-receiver | event-saver | `events.meeting.lifecycle` | 5 (NORMAL) | `MeetingUrlCreatedPayload` |
| `meeting.url_deleted` | event-receiver | event-saver | `events.meeting.lifecycle` | 5 (NORMAL) | `MeetingUrlDeletedPayload` |
| `notification.send_requested` | event-receiver | event-notifier (intended, not delivered) | `events.notification.commands` | 7 (HIGH) | `NotificationCommandPayload` |
| `notification.email.message_sent` | event-notifier (NOT implemented) | event-saver | `events.notification.delivery` | 7 (HIGH) | `EmailNotificationPayload` |
| `notification.telegram.message_sent` | event-notifier (NOT implemented) | event-saver | `events.notification.delivery` | 7 (HIGH) | `TelegramNotificationPayload` |
| `notification.push.message_sent` | event-notifier (NOT implemented) | event-saver | `events.notification.delivery` | 5 (NORMAL) | `PushNotificationPayload` |
| `unisender.events.v1.transactional.status.create` | event-receiver | event-saver | `events.mail` | 5 (NORMAL) | `UniSenderStatusPayload` |
| `getstream.channel.created` | event-receiver | event-saver | `events.chat` | 5 (NORMAL) | `GetStreamEventPayload` |
| `getstream.channel.deleted` | event-receiver | event-saver | `events.chat` | 5 (NORMAL) | `GetStreamEventPayload` |
| `getstream.message.new` | event-receiver | event-saver | `events.chat` | 5 (NORMAL) | `GetStreamEventPayload` |
| `getstream.message.updated` | event-receiver | none | `events.chat` | 5 (NORMAL) | `GetStreamEventPayload` |
| `getstream.message.deleted` | event-receiver | none | `events.chat` | 5 (NORMAL) | `GetStreamEventPayload` |
| `getstream.message.read` | event-receiver | event-saver | `events.chat` | 5 (NORMAL) | `GetStreamEventPayload` |
| `jitsi.conference.joined` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.conference.left` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.participant.joined` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.participant.left` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.participant.muted` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.participant.menu_button_click` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.audio.mute_status_changed` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.video.mute_status_changed` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.speaker.dominant_changed` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.device.list_changed` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.camera.error` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.mic.error` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.error.occurred` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.peer_connection.failure` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.suspend.detected` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `jitsi.toolbar.button_clicked` | jitsi-chat (via event-receiver) | event-saver | `events.jitsi` | 5 (NORMAL) | `JitsiEventPayload` |
| `user.email.change_requested` | event-admin (via event-receiver `/event/admin`) | event-users | `events.user.email` | 10 (CRITICAL) | `UserEmailChangeRequestedPayload` |
| _(unmatched)_ | event-receiver | event-saver (fallback) | `events.unrouted` | -- | raw payload |

**Source:** `docs/audit/CONTRACT_MAP.md:46-71`, `event-schemas/event_schemas/types.py:8-43`

---

## Event Detail: `user.email.change_requested`

Событие запроса смены email клиента, инициируемое администратором через `event-admin`.

| Атрибут | Значение |
|---------|----------|
| `ce-type` | `user.email.change_requested` |
| `ce-source` | `admin` |
| Queue | `events.user.email` |
| Priority | 10 (CRITICAL) |
| Producer | event-admin (через `POST /event/admin` в event-receiver) |
| Consumer | event-users (FastStream RabbitMQ consumer) |

**Payload schema (`UserEmailChangeRequestedPayload`)**:

```json
{
  "user_id": "uuid",
  "old_email": "old@example.com",
  "new_email": "new@example.com",
  "requested_by": "admin@example.com"
}
```

**Flow**:
1. Admin вызывает `POST /api/users/id/{user_id}/change-email` в event-admin.
2. event-admin публикует CloudEvent в event-receiver `POST /event/admin` (auth: static API key).
3. event-receiver маршрутизирует `admin` / `user.email.*` → `events.user.email`.
4. event-users потребляет событие: обновляет `users.email`, создаёт запись в `user_email_changelog`, устанавливает `email_source='admin'`.
5. Webhook outbox доставляет изменение в CRM; после успешной доставки сбрасывает `email_source='crm'`.

**CRM sync protection**: поле `email_source='admin'` блокирует перезапись email при следующей синхронизации с CRM до тех пор, пока outbox не доставит изменение.

---

## End-to-End Flow: Booking Created

```mermaid
sequenceDiagram
    participant Ext as External Booking Service
    participant ER as event-receiver
    participant EU as event-users
    participant RMQ as RabbitMQ
    participant ES as event-saver
    participant EN as event-notifier
    participant DB as PostgreSQL (main)

    Ext->>ER: POST /event/booking<br/>{type: booking.created, booking_uid, users[]}
    ER->>ER: Validate API key (X-API-Key header)
    ER->>ER: Parse payload, extract booking_uid

    loop For each participant email
        ER->>EU: GET /api/users/roles/{role}/emails/{email}
        EU-->>ER: {id: uuid} or 404 -> create user
    end

    ER->>ER: normalizers.normalize_booking_created()<br/>-> NormalizedPayload with participants
    ER->>ER: EventRouter.resolve_routing_key<br/>(source="booking", type="booking.created")
    Note over ER: First-match rule resolves to<br/>"events.notifications" (BUG: C-1)

    ER->>RMQ: publish(exchange="events",<br/>routing_key="events.notifications",<br/>priority=10, headers=ce-*)

    Note over RMQ: events.booking.lifecycle queue<br/>gets ZERO messages (shadowed)

    RMQ->>EN: deliver to events.notifications queue
    EN->>EN: NotificationConsumer._handle()<br/>-> DomainEvent{booking.created}
    EN->>EN: ProcessDomainEventUseCase:<br/>idempotency check, load routing_rules
    EN->>EU: GET /api/users/id/{user_id}
    EU-->>EN: contacts: [email, telegram]
    EN->>EN: Write outbox records (email + telegram)

    Note over ES: event-saver does NOT receive<br/>this event unless explicitly<br/>subscribed to events.notifications
```

**Intended flow (after C-1 fix):** Routing key would be `events.booking.lifecycle`, event-saver would consume it, and event-notifier would receive `notification.send_requested` via `events.notification.commands` instead.

**Source:** `docs/audit/CONTRACT_MAP.md:76-96`

---

## End-to-End Flow: Notification Send

```mermaid
sequenceDiagram
    participant Src as Any Source
    participant ER as event-receiver
    participant RMQ as RabbitMQ
    participant EN as event-notifier
    participant DB_N as PostgreSQL (notifier)
    participant EU as event-users
    participant Email as UniSender Go API
    participant TG as Telegram Bot API

    Src->>ER: POST /event/cloudevents<br/>{type: notification.send_requested}
    ER->>ER: JWT Bearer validation
    ER->>ER: EventRouter resolves -><br/>"events.notification.commands"
    ER->>RMQ: publish(routing_key=<br/>"events.notification.commands")

    Note over EN: event-notifier subscribes to<br/>"events.notification.commands" by default<br/>(queue mismatch C-3 resolved).

    RMQ->>EN: deliver message

    EN->>EN: from_http() -> CloudEvent
    EN->>EN: DOMAIN_EVENT_TO_TRIGGER map<br/>-> trigger_event string
    EN->>DB_N: Check processed_events<br/>(idempotency)
    EN->>DB_N: Load routing_rules(event_type)
    EN->>EN: apply_routing_rules()<br/>-> [(user_id, role), ...]

    loop Per recipient
        EN->>EU: GET /api/users/id/{user_id}
        EU-->>EN: ChannelContact[]
    end

    EN->>DB_N: write_outbox_atomically()<br/>(outbox records + mark processed)

    loop OutboxSender polls every 1s
        EN->>DB_N: SELECT ... FOR UPDATE SKIP LOCKED<br/>(inside transaction)
        EN->>Email: POST /email/send.json<br/>(template_id from _TEMPLATE_MAP)
        Email-->>EN: 200 OK
        EN->>TG: POST /bot{token}/sendMessage<br/>(hardcoded Russian text)
        TG-->>EN: 200 OK
        EN->>DB_N: UPDATE status='delivered'
    end

    Note over EN: Delivery result events<br/>(notification.*.message_sent)<br/>are NOT published back.<br/>Pipeline stops here.
```

**Source:** `docs/audit/CONTRACT_MAP.md:100-135`, `event-notifier/docs/SERVICE_OVERVIEW.md:22-52`

---

## Schema Versioning

### How It Works (In Theory)

1. `event-schemas/event_schemas/types.py:119-145` defines `EVENT_SCHEMA_VERSIONS`: a dict mapping every `EventType` to a semver string.
2. event-receiver's `CloudEventPublisher` embeds this version in the `dataschema` CloudEvent attribute as a URI (e.g., `urn:events:booking.created:v1`).
3. Intended semantics: major bump = breaking payload change, minor bump = additive change.

### How It Actually Works

All 25 event types are version `"v1"`. No consumer reads or validates the `dataschema` attribute. Version bumps have no operational effect on routing, parsing, or validation. There is no schema registry, no backward-compatibility enforcement, and no automated validation that a payload matches its declared schema.

**Source:** `event-schemas/docs/SERVICE_OVERVIEW.md:57-78`

---

## Known Inconsistencies

### IC-1: Booking lifecycle events routed to wrong queue [CRITICAL]

First-match routing in `event-receiver/event_receiver/config.py:9-34` sends `booking.created`, `booking.cancelled`, `booking.rescheduled`, `booking.reassigned`, `booking.reminder_sent` to `events.notifications` instead of `events.booking.lifecycle`.

### IC-2: event-notifier queue mismatch [RESOLVED]

`event-notifier/event_notifier/config.py:18` now correctly defaults to `events.notification.commands`, which matches the routing key event-receiver uses for `notification.send_requested` events. The previous default of `events.notifications` caused messages to pile up unconsumed.

### IC-3: Dual EventType enums [CRITICAL]

`event-schemas` defines `EventType.BOOKING_CREATED = "booking.created"` while `event-saver` defines `EventType.BOOKING_CREATED = "booking.events.v1.booking.created.create"` (`event-saver/event_saver/event_types.py:29`). The shared library is not used by its largest consumer.

### IC-4: Queue declaration argument mismatch

event-receiver creates queues with `x-max-priority=10` and DLX; event-saver creates plain durable queues. Same queue with different arguments causes RabbitMQ declaration conflict.

### IC-5: Missing delivery result pipeline

event-notifier's architecture describes publishing `notification.*.message_sent` events back to event-receiver. No publisher implementation exists. The `events.notification.delivery` queue is permanently empty.

### IC-6: Orphaned queues

| Queue | Why Orphaned |
|-------|-------------|
| `events.booking.lifecycle` | Routing never sends messages here (IC-1) |
| `events.booking.reminder` | Same routing bug (IC-1) |
| `events.notification.commands` | Previously orphaned (IC-2, resolved); event-notifier now consumes this queue by default |

**Full details:** `docs/audit/CONTRACT_MAP.md:139-207`

---

## How to Add a New Message Type

### Step 1: Define the Schema

Add a Pydantic model in the appropriate module under `event-schemas/event_schemas/`:
- Booking events: `booking.py`
- Chat events: `chat.py`
- Notification events: `notification.py`
- External integrations: `external.py`

Export from `__init__.py` via `__all__`.

### Step 2: Register the EventType

In `event-schemas/event_schemas/types.py`:
1. Add a member to `class EventType(StrEnum)` with the CloudEvent type string value
2. Add an entry to `EVENT_PRIORITIES` dict (choose CRITICAL=10, HIGH=7, NORMAL=5, or LOW=1)
3. Add an entry to `EVENT_SCHEMA_VERSIONS` dict (start at `"v1"`)

### Step 3: Add Routing Rule

In `event-receiver/event_receiver/config.py`, add a `RouteRule` to `_default_route_rules()`:

```python
RouteRule(
    destination="events.<target_queue>",
    source_pattern="<source_glob>",
    type_pattern="<type_glob_or_exact>",
),
```

**Important:** Rule position matters. First match wins. Place specific rules before broad globs.

### Step 4: Add Normalizer (if event-receiver validates payload)

In `event-receiver/event_receiver/normalizers.py`, add a normalization path that extracts participants and produces a `NormalizedPayload`.

### Step 5: Add Consumer Handling (event-saver)

1. Add the EventType string to `event-saver/event_saver/event_types.py` (note: uses different string format -- see IC-3)
2. Add routing rule in `event-saver/event_saver/config.py`
3. If new projection needed: create handler in `event-saver/event_saver/infrastructure/persistence/projections/`, register in `ioc.py`

### Step 6: Declare Queue (if new)

If the target queue is new:
- event-receiver's topology manager auto-declares queues derived from routing destinations
- event-saver's `config.py` `_default_route_rules()` must include a rule targeting the new queue (or set `RABBIT_TOPOLOGY_QUEUES` explicitly)

### Step 7: Update Documentation

- `event-receiver/QUEUES_DIGEST.md`
- `event-saver/QUEUES_DIGEST.md`
- `event-receiver/EVENTS_DIGEST.md`
- This file (MESSAGE_CONTRACTS.md)
