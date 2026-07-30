from datetime import datetime
from zoneinfo import ZoneInfo

import config


def app_tz() -> ZoneInfo:
    return ZoneInfo(config.APP_TZ)


def now() -> datetime:
    return datetime.now(app_tz())


def now_iso() -> str:
    return now().isoformat()


def format_local(value: str | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:19].replace("T", " ")
    if dt.tzinfo is None:
        # Legacy naive timestamps were written as UTC.
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(app_tz()).strftime(fmt)
