"""Weeks, Finnish holidays and number formats.

A week runs Monday..Sunday and is keyed by its Monday; people see its ISO
week number ("Week 39"). Dates print Finnish style (27.9.2026) and hours
with a decimal comma (7,50)."""
import math
import re
from datetime import date, timedelta

DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DAY_TITLES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MAX_HOURS_PER_DAY = 24
DEFAULT_FULL_WEEK = 37.5  # the usual Finnish full-time week: 5 x 7,5 h

_HOURS_RE = re.compile(r"^(\d{0,2})(?:([.,])(\d{0,2})|:(\d{0,2}))?h?$")


# ---- weeks -----------------------------------------------------------------

def week_start(d: date) -> date:
    """The Monday of d's week."""
    return d - timedelta(days=d.weekday())


def week_days(start: date) -> list[date]:
    return [start + timedelta(days=i) for i in range(7)]


def week_label(start: date, today: date | None = None) -> str:
    """"Week 39", with the ISO year added when it isn't the current one
    ("Week 1, 2027"), so weeks around New Year can't be mixed up."""
    iso = start.isocalendar()
    this_year = (today or date.today()).isocalendar().year
    return f"Week {iso.week}" + (f", {iso.year}" if iso.year != this_year else "")


def week_range(start: date) -> str:
    """The week's dates, e.g. 21.9.–27.9.2026."""
    return f"{fmt_day(start)}.–{fmt_date(start + timedelta(days=6))}"


def iso_week(start: date) -> str:
    """ISO 8601 week notation, e.g. 2026-W39 -- unambiguous in exports."""
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# ---- formatting ------------------------------------------------------------

def fmt_date(d: date) -> str:
    return f"{d.day}.{d.month}.{d.year}"


def fmt_day(d: date) -> str:
    return f"{d.day}.{d.month}"


def fmt_hours(hours: float, blank_zero: bool = False) -> str:
    if blank_zero and not hours:
        return ""
    return f"{hours:.2f}".replace(".", ",")


def fmt_signed(hours: float) -> str:
    """Hours with a sign, for balances: +3,50 / −2,00 (a real minus) / 0,00."""
    hours = round(hours, 2)
    return fmt_hours(0) if not hours else ("+" if hours > 0 else "−") + fmt_hours(abs(hours))


def fmt_percent(part: float, whole: float) -> str:
    """part's share of whole as a whole-number percentage, Finnish style
    with a non-breaking space ("52 %"). Blank when there's nothing to
    compare; "<1 %" rather than a misleading "0 %" for a sliver."""
    if not part or not whole:
        return ""
    share = 100 * part / whole
    return "<1 %" if share < 0.5 else f"{round(share)} %"


def parse_hours(text: str) -> float:
    """What people naturally type into an hours box: 7,5 / 7.5 / 7:30
    (hours:minutes) / 8h / empty (= 0). Raises ValueError for anything
    else, or anything over 24 h."""
    t = text.strip().replace(" ", "").lower()
    if not t:
        return 0.0
    m = _HOURS_RE.match(t)
    if not m or t in (".", ",", ":", "h"):
        raise ValueError(f"'{text}' isn't a number of hours")
    whole, _sep, frac, minutes = m.groups()
    hours = float(whole or 0)
    if frac:
        hours += float(f"0.{frac}")
    if minutes:
        mins = int(minutes)
        if mins >= 60:
            raise ValueError(f"'{text}' has more than 59 minutes")
        hours += mins / 60
    if hours > MAX_HOURS_PER_DAY:
        raise ValueError(f"{fmt_hours(hours)} is more than {MAX_HOURS_PER_DAY} hours")
    return round(hours, 2)


def step_half_hours(hours: float, steps: int) -> float:
    """Moves hours by whole half hours, as the mouse wheel does. An odd
    value first snaps to the half hour in that direction (7,25 up -> 7,50,
    down -> 7,00). Stays within 0..24."""
    for _ in range(abs(steps)):
        if steps > 0:
            hours = math.floor(hours * 2 + 1e-9) / 2 + 0.5
        else:
            hours = math.ceil(hours * 2 - 1e-9) / 2 - 0.5
    return min(float(MAX_HOURS_PER_DAY), max(0.0, hours))


# ---- Finnish holidays ------------------------------------------------------

def _easter(year: int) -> date:
    # anonymous Gregorian algorithm
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _saturday_between(year: int, month: int, first_day: int) -> date:
    d = date(year, month, first_day)
    return d + timedelta(days=(5 - d.weekday()) % 7)


_holiday_cache: dict[int, tuple[dict[date, str], dict[date, str]]] = {}


def _holidays(year: int) -> tuple[dict[date, str], dict[date, str]]:
    """(public holidays, every holiday day off) for one year."""
    if year not in _holiday_cache:
        easter = _easter(year)
        midsummer = _saturday_between(year, 6, 20)
        public = {
            date(year, 1, 1): "New Year's Day (Uudenvuodenpäivä)",
            date(year, 1, 6): "Epiphany (Loppiainen)",
            easter - timedelta(days=2): "Good Friday (Pitkäperjantai)",
            easter: "Easter Sunday (Pääsiäispäivä)",
            easter + timedelta(days=1): "Easter Monday (2. pääsiäispäivä)",
            date(year, 5, 1): "May Day (Vappu)",
            easter + timedelta(days=39): "Ascension Day (Helatorstai)",
            easter + timedelta(days=49): "Whit Sunday (Helluntaipäivä)",
            midsummer: "Midsummer Day (Juhannuspäivä)",
            _saturday_between(year, 10, 31): "All Saints' Day (Pyhäinpäivä)",
            date(year, 12, 6): "Independence Day (Itsenäisyyspäivä)",
            date(year, 12, 25): "Christmas Day (Joulupäivä)",
            date(year, 12, 26): "Boxing Day (Tapaninpäivä)",
        }
        # printed black in calendars, but holidays under the Annual
        # Holidays Act (vuosilomalaki 4 §)
        eves = {
            easter - timedelta(days=1): "Easter Saturday (Pääsiäislauantai)",
            midsummer - timedelta(days=1): "Midsummer Eve (Juhannusaatto)",
            date(year, 12, 24): "Christmas Eve (Jouluaatto)",
        }
        _holiday_cache[year] = (public, {**public, **eves})
    return _holiday_cache[year]


def holidays(year: int) -> dict[date, str]:
    """Every holiday day off: public holidays plus the three eves."""
    return _holidays(year)[1]


def holiday_name(d: date) -> str | None:
    return holidays(d.year).get(d)


def is_public_holiday(d: date) -> bool:
    """An official public holiday -- a red day in Finnish calendars."""
    return d in _holidays(d.year)[0]


def holiday_tooltip(d: date) -> str | None:
    name = holiday_name(d)
    if name and not is_public_holiday(d):
        return f"{name}\nHoliday under the Annual Holidays Act (vuosilomalaki), not a red calendar day"
    return name


def is_day_off(d: date) -> bool:
    """Weekend or holiday: shaded, and never given planned hours."""
    return d.weekday() >= 5 or holiday_name(d) is not None


def week_capacity(start: date, full_week: float = DEFAULT_FULL_WEEK) -> float:
    """Hours a full-time week holds once holidays are taken out: a fifth
    of full_week for every Monday-Friday that isn't a holiday."""
    working_days = sum(1 for d in week_days(start) if not is_day_off(d))
    return round(full_week / 5 * working_days, 2)
