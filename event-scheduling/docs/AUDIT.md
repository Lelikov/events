# event-scheduling: Audit

## Status

New service (added 2026-07-03). No audit findings at this time.

## Known Gaps (by design — slice 1 scope)

These are intentional deferred items, not bugs or security issues:

| Gap | Status | Target slice |
|-----|--------|--------------|
| event_type ETL from cal.com | Deferred — `scripts/etl_from_calcom.py` migrates schedules only; EventType/Host/BookingLimit migration is a separate future branch | Slice 1 follow-up |
| `BusyTimesSource` is a stub | `StubBusyTimesSource` returns `[]` always; no real busy-time calculation | Slice 3 |
| No slot engine | Slot availability calculation is out of scope | Slice 2 |
| Static single API key | No per-caller keys, no rotation | Accepted for slice 1 |
| No CloudEvent emission | Schedule change events are not published (no consumers yet) | When slice 2 requires them |
| `host.schedule_id` ON DELETE RESTRICT | Prevents deleting a schedule while assigned to a host; requires explicit host removal first | Acceptable constraint |

## Security

- API key gate uses `hmac.compare_digest` (constant-time); no timing oracle.
- No user-supplied SQL; all queries use `text()` with bound parameters.
- No secrets stored in the DB.

## Future Audit

When the service reaches a maturity milestone, run a full audit following the
`docs/audit/v2/AUDIT_REPORT_V2.md` methodology and record findings here.

## Slice-1 deferred follow-ups (tracked)

These items are intentionally deferred from the slice-1 implementation and are
tracked here so they are not lost:

1. **event_type/host/booking_limit ETL branch not implemented** — `scripts/etl_from_calcom.py`
   migrates the schedule aggregate only. EventType/Host/BookingLimit migration is a
   separate future branch.

2. **`event_type_db.list_all` is N+1** — `list_all` issues 1 + 2N queries (one
   per event_type for hosts and limits). Batch with
   `WHERE event_type_id = ANY(:ids)` when the catalog grows or the endpoint goes hot.

3. **`validate_time_zone` — harden against corrupt tzdata** — `ZoneInfo(tz)` is called
   after the `available_timezones()` membership check but is not wrapped in
   `try/except`. Wrap in `try/except ZoneInfoNotFoundError → ValidationError` to
   guard against corrupt tzdata (practically unreachable today).

4. **Test coverage gaps** — `test_etl_mapping` should exercise the unknown-key skip
   path in `expand_booking_limits`; `test_schema` CHECK-violation test could use a
   `SAVEPOINT` to avoid poisoning the transaction on constraint error.
