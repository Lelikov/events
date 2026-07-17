import json
from uuid import UUID

from event_scheduling.booking_fields.dto import BookingFieldDTO, OptionDTO, UpsertBookingFieldDTO
from event_scheduling.interfaces.sql import ISqlExecutor


def _row_to_dto(r) -> BookingFieldDTO:  # noqa: ANN001
    raw = r["options"]
    opts = [OptionDTO(value=o["value"], label=o["label"]) for o in (raw or [])]
    return BookingFieldDTO(
        field_key=r["field_key"],
        field_type=r["field_type"],
        label=r["label"],
        placeholder=r["placeholder"],
        required=r["required"],
        options=opts,
        position=r["position"],
    )


class BookingFieldAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def list_for(self, event_type_id: UUID) -> list[BookingFieldDTO]:
        rows = await self._sql.fetch_all(
            "SELECT field_key, field_type, label, placeholder, required, options, position "
            "FROM booking_field WHERE event_type_id = :et ORDER BY position",
            {"et": event_type_id},
        )
        return [_row_to_dto(r) for r in rows]

    async def event_type_exists(self, event_type_id: UUID) -> bool:
        row = await self._sql.fetch_one("SELECT 1 AS ok FROM event_type WHERE id = :id", {"id": event_type_id})
        return row is not None

    async def replace(
        self, event_type_id: UUID, items: list[UpsertBookingFieldDTO], keys: list[str]
    ) -> list[BookingFieldDTO]:
        await self._sql.execute("DELETE FROM booking_field WHERE event_type_id = :et", {"et": event_type_id})
        for position, (item, key) in enumerate(zip(items, keys, strict=True)):
            options_json = (
                json.dumps([{"value": o.value, "label": o.label} for o in item.options]) if item.options else None
            )
            await self._sql.execute(
                "INSERT INTO booking_field "
                "(event_type_id, field_key, field_type, label, placeholder, required, options, position) "
                "VALUES (:et, :k, :ft, :lbl, :ph, :req, CAST(:opts AS JSONB), :pos)",
                {
                    "et": event_type_id,
                    "k": key,
                    "ft": item.field_type,
                    "lbl": item.label,
                    "ph": item.placeholder,
                    "req": item.required,
                    "opts": options_json,
                    "pos": position,
                },
            )
        return await self.list_for(event_type_id)
