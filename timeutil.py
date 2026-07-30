from datetime import datetime, timedelta
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


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(app_tz())


def previous_aligned(moment: datetime | None = None, interval_minutes: int | None = None) -> datetime:
    """Most recent wall-clock slot at/before now (aligned from local midnight)."""
    moment = moment or now()
    interval_minutes = interval_minutes or config.AUTOPILOT_INTERVAL_MINUTES
    interval = max(1, int(interval_minutes)) * 60
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (moment - midnight).total_seconds()
    steps = int(elapsed // interval)
    return midnight + timedelta(seconds=steps * interval)


def next_aligned(moment: datetime | None = None, interval_minutes: int | None = None) -> datetime:
    """Next wall-clock slot strictly after now."""
    moment = moment or now()
    interval_minutes = interval_minutes or config.AUTOPILOT_INTERVAL_MINUTES
    interval = max(1, int(interval_minutes)) * 60
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (moment - midnight).total_seconds()
    steps = int(elapsed // interval) + 1
    return midnight + timedelta(seconds=steps * interval)


def missed_schedule(last_run_iso: str | None, interval_minutes: int | None = None) -> bool:
    """True if we never ran for the latest due slot."""
    last_run = parse_iso(last_run_iso)
    due = previous_aligned(interval_minutes=interval_minutes)
    if last_run is None:
        return True
    return last_run < due
