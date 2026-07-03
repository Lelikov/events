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
