from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core import config


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


def is_weekend(moment: datetime | None = None) -> bool:
    moment = moment or now()
    return moment.weekday() >= 5  # Saturday=5, Sunday=6


def _minutes_of_day(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def _in_local_window(
    moment: datetime,
    tz_name: str,
    start: tuple[int, int],
    end: tuple[int, int],
) -> bool:
    local = moment.astimezone(ZoneInfo(tz_name))
    if local.weekday() >= 5:
        return False
    minutes = _minutes_of_day(local)
    start_m = start[0] * 60 + start[1]
    end_m = end[0] * 60 + end[1]
    return start_m <= minutes < end_m


def equity_market_status(moment: datetime | None = None) -> dict:
    """Rough equity session check for mixed US/EU Trading212 books.

    Open if a weekday falls in US extended hours (04:00–20:00 ET, matching
    market orders with extendedHours) or Europe regular hours (09:00–17:30
    Berlin). Holidays are not detected.
    """
    moment = moment or now()
    us_extended = _in_local_window(
        moment, "America/New_York", (4, 0), (20, 0)
    )
    eu_regular = _in_local_window(
        moment, "Europe/Berlin", (9, 0), (17, 30)
    )
    ny_weekend = moment.astimezone(ZoneInfo("America/New_York")).weekday() >= 5
    eu_weekend = moment.astimezone(ZoneInfo("Europe/Berlin")).weekday() >= 5
    weekend = ny_weekend and eu_weekend
    open_now = us_extended or eu_regular
    warning = None
    if weekend:
        warning = (
            "Markets are closed for the weekend. Reset may fail to sell positions "
            "and could still erase AI memory."
        )
    elif not open_now:
        warning = (
            "Equity markets look closed (US extended 04:00–20:00 ET, Europe "
            "09:00–17:30). Reset may fail to sell some or all positions."
        )
    return {
        "open": open_now,
        "weekend": weekend,
        "us_extended": us_extended,
        "eu_regular": eu_regular,
        "warning": warning,
    }


def next_trading_aligned(moment: datetime | None = None, interval_minutes: int | None = None) -> datetime:
    """Next aligned slot that falls on a weekday."""
    t = next_aligned(moment, interval_minutes)
    while is_weekend(t):
        # Jump to Monday 00:00, then take the next aligned slot.
        days = 7 - t.weekday()  # Sat -> 2, Sun -> 1
        monday = (t + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        t = next_aligned(monday - timedelta(seconds=1), interval_minutes)
    return t
