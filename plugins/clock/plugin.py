"""Current date and time — models don't know what day it is."""
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from app.plugins.sdk import tool

DESCRIPTION = "Current date and time in any time zone."


@tool
def now(
    timezone: Annotated[str, Field(description="IANA time zone, e.g. Africa/Douala, Europe/Paris, UTC")] = "UTC",
) -> dict:
    """The current date, time and weekday in a time zone."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone {timezone!r}; use an IANA name like Africa/Douala") from exc
    moment = datetime.now(zone)
    return {
        "timezone": timezone,
        "iso": moment.isoformat(timespec="seconds"),
        "date": moment.strftime("%Y-%m-%d"),
        "time": moment.strftime("%H:%M"),
        "weekday": moment.strftime("%A"),
    }
