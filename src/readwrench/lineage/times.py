"""Capture timestamps: strict ISO validation and conservative legacy date parsing.

``iso_time`` reproduces the original zod ``z.iso.datetime({ offset: true })`` check
followed by ``new Date(value).toISOString()``. ``legacy_time`` replaces the original's
luxon parsing (``fromISO``, ``fromRFC2822``, ``fromHTTP`` and one ``fromFormat`` for
SingleFile's JavaScript ``Date.toString()`` stamp), including luxon's weekday/calendar
consistency check. Date arithmetic is proleptic Gregorian in plain integers, so years
outside 1..9999 serialize like JavaScript's extended ISO years.
"""
from __future__ import annotations

import re

from .fileio import InputError

_DATE = (r"(?:(?:[0-9][0-9][2468][048]|[0-9][0-9][13579][26]|[0-9][0-9]0[48]|[02468][048]00"
         r"|[13579][26]00)-02-29|[0-9]{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])"
         r"|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)|(?:02)-(?:0[1-9]|1[0-9]|2[0-8])))")
_TIME = r"(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9](?:\.[0-9]+)?)?"
_OFFSET = r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
TIMESTAMP = re.compile(f"^{_DATE}T(?:{_TIME}{_OFFSET})$")
_PARTS = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::(\d\d)(?:\.(\d+))?)?(?:Z|([+-])(\d\d):(\d\d))$",
                    re.ASCII)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS_LONG = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _days_from_civil(year: int, month: int, day: int) -> int:
    year -= month <= 2
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(days: int) -> tuple[int, int, int]:
    days += 719468
    era = (days if days >= 0 else days - 146096) // 146097
    doe = days - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    year = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + (3 if mp < 10 else -9)
    return year + (month <= 2), month, day


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0 else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _epoch_ms(year, month, day, hour, minute, second, millis, offset_minutes) -> int:
    days = _days_from_civil(year, month, day)
    return ((days * 24 + hour) * 60 + minute - offset_minutes) * 60_000 + second * 1000 + millis


def iso_string(ms: int) -> str:
    """``Date.prototype.toISOString`` for an epoch value in milliseconds."""
    days, rest = divmod(ms, 86_400_000)
    year, month, day = _civil_from_days(days)
    hour, rest = divmod(rest, 3_600_000)
    minute, rest = divmod(rest, 60_000)
    second, millis = divmod(rest, 1000)
    if 0 <= year <= 9999:
        year_text = f"{year:04d}"
    else:
        year_text = f"{'-' if year < 0 else '+'}{abs(year):06d}"
    return f"{year_text}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}.{millis:03d}Z"


def iso_time(value, source: str) -> str:
    if not isinstance(value, str) or len(value) > 100:
        raise InputError(f"Capture timestamp from {source} must be a string of at most 100 characters")
    match = _PARTS.match(value) if TIMESTAMP.match(value) else None
    if not match:
        raise InputError(f"Invalid timezone-qualified capture time from {source}: {value}")
    year, month, day, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    millis = int((match.group(7) or "0")[:3].ljust(3, "0"))
    offset = 0
    if match.group(8):
        offset = (int(match.group(9)) * 60 + int(match.group(10))) * (1 if match.group(8) == "+" else -1)
    return iso_string(_epoch_ms(year, month, day, hour, minute, second, millis, offset))


# --- legacy (MIME Date / SingleFile saved-date) parsing -------------------------------

_HAS_ZONE = re.compile(r"(?:[+-][0-9]{2}:?[0-9]{2}|\b(?:GMT|UTC)\b|Z$)", re.IGNORECASE | re.ASCII)
_ISO = re.compile(
    r"^([+-]\d{6}|\d{4})(?:-(\d\d)-(\d\d)|(\d\d)(\d\d))"
    r"T(\d\d)(?::?(\d\d)(?::?(\d\d)(?:[.,](\d+))?)?)?"
    r"(Z|[+-]\d\d(?::?\d\d)?)$", re.ASCII)
_RFC2822 = re.compile(
    r"^(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s)?(\d{1,2})\s(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s(\d{2,4})\s(\d\d):(\d\d)(?::(\d\d))?\s(?:(UT|GMT|[ECMP][SD]T)|([Zz])|(?:([+-]\d\d)(\d\d)))$", re.ASCII)
_OBSOLETE = {"UT": 0, "GMT": 0, "EDT": -240, "EST": -300, "CDT": -300, "CST": -360, "MDT": -360,
             "MST": -420, "PDT": -420, "PST": -480}
_MONTH_ALT = "|".join(MONTHS)
_RFC1123 = re.compile(rf"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\d\d) ({_MONTH_ALT}) (\d{{4}}) (\d\d):(\d\d):(\d\d) GMT$",
                      re.ASCII)
_RFC850 = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), (\d\d)-"
                     rf"({_MONTH_ALT})-(\d\d) (\d\d):(\d\d):(\d\d) GMT$", re.ASCII)
_ASCTIME = re.compile(rf"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ({_MONTH_ALT}) ( \d|\d\d) (\d\d):(\d\d):(\d\d) (\d{{4}})$",
                      re.ASCII)
_JS_DATE = re.compile(rf"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ({_MONTH_ALT}) (\d\d) (\d{{4}}) (\d\d):(\d\d):(\d\d) "
                      r"GMT([+-]\d{1,2})(\d\d)?$", re.IGNORECASE | re.ASCII)


def _untruncate(text: str) -> int:
    year = int(text)
    if len(text) == 2:
        return 1900 + year if year > 60 else 2000 + year
    return year


def _build(year, month, day, hour, minute, second, millis, offset, weekday=None):
    """Epoch milliseconds, or None if luxon would call the result invalid."""
    if not 1 <= month <= 12 or not 1 <= day <= _days_in_month(year, month):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    if weekday is not None and (_days_from_civil(year, month, day) + 3) % 7 + 1 != weekday:
        return None
    return _epoch_ms(year, month, day, hour, minute, second, millis, offset)


def _month(name: str) -> int:
    return [m.lower() for m in MONTHS].index(name.lower()) + 1


def _from_iso(value: str):
    match = _ISO.match(value)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2) or match.group(4))
    day = int(match.group(3) or match.group(5))
    hour = int(match.group(6))
    minute = int(match.group(7) or 0)
    second = int(match.group(8) or 0)
    millis = int((match.group(9) or "0")[:3].ljust(3, "0"))
    zone = match.group(10)
    offset = 0
    if zone != "Z":
        digits = zone[1:].replace(":", "")
        offset = (int(digits[:2]) * 60 + int(digits[2:4] or 0)) * (1 if zone[0] == "+" else -1)
    return _build(year, month, day, hour, minute, second, millis, offset)


def _from_rfc2822(value: str):
    text = re.sub(r"\([^()]*\)|[\n\t]", " ", value)
    text = re.sub(r"(\s\s+)", " ", text).strip()
    match = _RFC2822.match(text)
    if not match:
        return None
    weekday, day, month, year, hour, minute, second, obsolete, military, off_hour, off_minute = match.groups()
    if obsolete:
        offset = _OBSOLETE[obsolete]
    elif military:
        offset = 0
    else:
        hours = int(off_hour)
        offset = hours * 60 + (-1 if hours < 0 or off_hour.startswith("-") else 1) * int(off_minute)
    return _build(_untruncate(year), _month(month), int(day), int(hour), int(minute), int(second or 0), 0,
                  offset, WEEKDAYS.index(weekday) + 1 if weekday else None)


def _from_http(value: str):
    match = _RFC1123.match(value)
    if match:
        weekday, day, month, year, hour, minute, second = match.groups()
        return _build(int(year), _month(month), int(day), int(hour), int(minute), int(second), 0, 0,
                      WEEKDAYS.index(weekday) + 1)
    match = _RFC850.match(value)
    if match:
        weekday, day, month, year, hour, minute, second = match.groups()
        return _build(_untruncate(year), _month(month), int(day), int(hour), int(minute), int(second), 0, 0,
                      WEEKDAYS_LONG.index(weekday) + 1)
    match = _ASCTIME.match(value)
    if match:
        weekday, month, day, hour, minute, second, year = match.groups()
        return _build(int(year), _month(month), int(day.strip()), int(hour), int(minute), int(second), 0, 0,
                      WEEKDAYS.index(weekday) + 1)
    return None


def _from_js_date(value: str):
    match = _JS_DATE.match(re.sub(r"\s*\([^)]*\)\s*$", "", value))
    if not match:
        return None
    weekday, month, day, year, hour, minute, second, off_hour, off_minute = match.groups()
    hours = int(off_hour)
    offset = hours * 60 + (-1 if off_hour.startswith("-") else 1) * int(off_minute or 0)
    return _build(int(year), _month(month), int(day), int(hour), int(minute), int(second), 0, offset,
                  [w.lower() for w in WEEKDAYS].index(weekday.lower()) + 1)


def legacy_time(value, source: str):
    """A MIME Date or SingleFile saved-date claim, only with an explicit timezone."""
    if not isinstance(value, str) or len(value) > 500:
        return None
    # An explicit numeric offset or GMT/UTC is required; locale-only dates stay unknown.
    if not _HAS_ZONE.search(value):
        return None
    for parser in (_from_iso, _from_rfc2822, _from_http, _from_js_date):
        ms = parser(value)
        if ms is not None:
            return {"at": iso_string(ms), "source": source, "raw": value}
    return None
