from dataclasses import dataclass


@dataclass(frozen=True)
class BookingLimitDTO:
    limit_type: str
    period: str
    value: int
