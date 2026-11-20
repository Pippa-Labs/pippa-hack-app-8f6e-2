"""Quiet Time: how busy a London tube station is at a time and day, compared
with that station's usual peak, and when it is quietest or busiest.

Every figure comes from `crowding.json`, beside this file: TfL's open crowding
data (Powered by TfL Open Data), downloaded ahead of time with
`scripts/build_data.py`. TfL answers 403 to Pippa's servers, so this app never
makes a network call at all: it reads the bundled file and works the answer
out from it.

TfL publishes, for each station and day of the week, 96 fifteen-minute bands,
each a fraction of a baseline that is not the station's own peak (Bank tops
out near 61, Euston near 14). So every figure is rescaled here: a station's
busiest fifteen minutes in a normal week counts as 100%, and `busyness_pct`
is the percentage of that.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pippa_agentapp_sdk import (
    AgentAppError,
    Call,
    Manifest,
    Result,
    load_manifest_file,
    validate_manifest,
)

MANIFEST_PATH = Path(__file__).with_name("manifest.yaml")
DATA_PATH = Path(__file__).with_name("crowding.json")
LONDON = ZoneInfo("Europe/London")

OUTCOMES = ("crowding.at", "crowding.quietest")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_NAMES = dict(zip(DAYS, ("Monday", "Tuesday", "Wednesday", "Thursday",
                            "Friday", "Saturday", "Sunday")))
WEEKDAY, WEEKEND = "Weekday", "Weekend"
GROUP_DAYS = {WEEKDAY: DAYS[:5], WEEKEND: DAYS[5:]}
GROUP_LABELS = {WEEKDAY: "Weekdays (Mon-Fri average)", WEEKEND: "Weekend (Sat-Sun average)"}

BANDS = 96                 # fifteen-minute bands in a day
BAND = 15                  # minutes per band
DAY_END = 24 * 60

# Levels, as a percent of the station's own typical peak.
QUIET_BELOW, MODERATE_BELOW, BUSY_BELOW = 30, 60, 85

# The window used for "quietest" when the question names neither a window nor
# a time: the hours people actually travel, not 03:00 when the station is shut.
DEFAULT_WINDOW = (6 * 60, 23 * 60)
# With a time but no window, "quietest" looks this far either side of it.
NEAR_TIME = 2 * 60


# ── Errors: one plain sentence each, naming what to retype ─────────────────


class QuietTimeError(AgentAppError):
    """Base for this app's own refusals."""

    user_message = "Quiet Time could not work that out from TfL's figures."
    retryable = False


class NoStation(QuietTimeError):
    user_message = "Which tube station? Name one, for example Oxford Circus or King's Cross."


class UnknownStation(QuietTimeError):
    user_message = "I couldn't find a London tube station with that name."


class NoCrowdingData(QuietTimeError):
    user_message = "TfL has no crowding figures for that station."


class UnreadableDay(QuietTimeError):
    user_message = ("I couldn't tell which day you meant; try a day such as Friday, "
                    "or weekday or weekend.")


class UnreadableTime(QuietTimeError):
    user_message = "I couldn't read that time; try something like 5:30pm or 17:30."


class DataUnavailable(QuietTimeError):
    user_message = "Quiet Time's copy of TfL's crowding figures is missing, so it cannot answer just now."


def _fail(cls: type[QuietTimeError], internal: str, message: str | None = None) -> QuietTimeError:
    error = cls(internal)
    if message:
        error.user_message = message
    return error


# ── The bundled data ────────────────────────────────────────────────────────


def load_data(path: Path = DATA_PATH) -> dict:
    """crowding.json, or an empty dataset if it is missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"downloaded": "", "stations": {}, "no_data": {}}
    data.setdefault("stations", {})
    data.setdefault("no_data", {})
    data.setdefault("downloaded", "")
    return data


DATA = load_data()


# ── Station names ───────────────────────────────────────────────────────────

#: Words shortened the same way on both sides, so "Tottenham Ct Rd" and
#: "Tottenham Court Road" compare equal.
_SHORT = {
    "street": "st", "saint": "st", "road": "rd", "court": "ct", "square": "sq",
    "terminal": "t", "terminals": "t", "term": "t", "cross": "x", "lane": "ln",
    "avenue": "ave", "gardens": "gdns", "garden": "gdn", "junction": "jn",
    "centre": "ctr", "center": "ctr",
}
#: Words that never distinguish one station from another.
_DROP = {"and", "the", "station", "stn", "underground", "tube", "lu", "line", "london"}

#: Ways people name stations that no spelling rule reaches. Keys are written
#: naturally and normalised when the index is built; values are TfL's names.
ALIASES = {
    "kx": "King's Cross St. Pancras",
    "kgx": "King's Cross St. Pancras",
    "kings x": "King's Cross St. Pancras",
    "st pancras": "King's Cross St. Pancras",
    "tcr": "Tottenham Court Road",
    "tott ct rd": "Tottenham Court Road",
    "heathrow": "Heathrow Terminals 2 & 3",
    "heathrow airport": "Heathrow Terminals 2 & 3",
    "lhr": "Heathrow Terminals 2 & 3",
    "heathrow central": "Heathrow Terminals 2 & 3",
    "heathrow t2": "Heathrow Terminals 2 & 3",
    "heathrow t3": "Heathrow Terminals 2 & 3",
    "heathrow t23": "Heathrow Terminals 2 & 3",
    "heathrow 2": "Heathrow Terminals 2 & 3",
    "heathrow 3": "Heathrow Terminals 2 & 3",
    "t2": "Heathrow Terminals 2 & 3",
    "t3": "Heathrow Terminals 2 & 3",
    "heathrow t4": "Heathrow Terminal 4",
    "heathrow 4": "Heathrow Terminal 4",
    "t4": "Heathrow Terminal 4",
    "heathrow t5": "Heathrow Terminal 5",
    "heathrow 5": "Heathrow Terminal 5",
    "t5": "Heathrow Terminal 5",
    "elephant": "Elephant & Castle",
    "e and c": "Elephant & Castle",
    "highbury": "Highbury & Islington",
    "olympia": "Kensington (Olympia)",
    "wembley": "Wembley Park",
    "bank and monument": "Bank",
    "bank monument": "Bank",
    "the angel": "Angel",
    "kings cross": "King's Cross St. Pancras",
    "oxford st": "Oxford Circus",
    "picadilly": "Piccadilly Circus",
    "piccadilly": "Piccadilly Circus",
    "leicester": "Leicester Square",
    "tower hill": "Tower Hill",
    "canary": "Canary Wharf",
    "westfield": "Wood Lane",
    "battersea": "Battersea Power Station",
}


def normalise(text: str) -> str:
    """Lower case, no accents or apostrophes, '&' as 'and', common words shortened."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ").replace("+", " and ")
    text = re.sub(r"['`’]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    # "T5" and "terminal5" both become "t 5".
    text = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", text)
    words = [_SHORT.get(word, word) for word in text.split()]
    return " ".join(word for word in words if word not in _DROP)


def _peak_raw(station: dict) -> int:
    values = [v for day in station.get("days", {}).values() for v in day]
    return max(values) if values else 0


class StationIndex:
    """Every way of naming a station, mapped to its NaPTAN id."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.names: dict[str, str] = {}        # naptan -> display name
        self.keys: dict[str, list[str]] = {}   # normalised key -> naptans
        everything = {**{k: v.get("name", k) for k, v in data.get("stations", {}).items()},
                      **{k: v for k, v in data.get("no_data", {}).items() if k not in data.get("stations", {})}}
        counts = Counter(everything.values())
        for naptan, name in everything.items():
            display = name
            station = data.get("stations", {}).get(naptan)
            if counts[name] > 1 and station and station.get("lines"):
                display = f"{name} ({', '.join(station['lines'])})"
            self.names[naptan] = display
            for key in {normalise(name), normalise(re.sub(r"\(.*?\)", " ", name)), normalise(display)}:
                if key:
                    self.keys.setdefault(key, []).append(naptan)
        by_name = {name: naptan for naptan, name in everything.items()}
        for alias, target in ALIASES.items():
            naptan = by_name.get(target)
            key = normalise(alias)
            if naptan and key and key not in self.keys:
                self.keys[key] = [naptan]

    def _best(self, naptans: list[str]) -> str:
        """Of several stations sharing a name, the busiest (the main one)."""
        stations = self.data.get("stations", {})
        return max(naptans, key=lambda n: (_peak_raw(stations.get(n, {})), n))

    def find(self, text: str) -> tuple[str | None, str | None]:
        """(naptan, None) for a match, or (None, suggestion name or None)."""
        query = normalise(text)
        if not query:
            return None, None
        if query in self.keys:
            return self._best(self.keys[query]), None
        if len(query) >= 3:
            prefixed = [k for k in self.keys if k.startswith(query + " ") or
                        (len(query) >= 5 and k.startswith(query))]
            if prefixed:
                shortest = min(len(k) for k in prefixed)
                naptans = [n for k in prefixed if len(k) == shortest for n in self.keys[k]]
                return self._best(naptans), None
            padded = f" {query} "
            inside = [k for k in self.keys if padded in f" {k} "]
            if inside:
                shortest = min(len(k) for k in inside)
                naptans = [n for k in inside if len(k) == shortest for n in self.keys[k]]
                return self._best(naptans), None
        scored = []
        for key in self.keys:
            score = difflib.SequenceMatcher(None, query, key).ratio()
            if len(query) >= 5:
                head = key[: len(query)]
                score = max(score, 0.95 * difflib.SequenceMatcher(None, query, head).ratio())
            scored.append((score, -len(key), key))
        if not scored:
            return None, None
        score, _, key = max(scored)
        if score >= 0.82:
            return self._best(self.keys[key]), None
        if score >= 0.6:
            return None, self.names[self._best(self.keys[key])]
        return None, None


# ── Days, times and windows ─────────────────────────────────────────────────

_DAY_WORDS = {
    "monday": "Mon", "mon": "Mon", "mondays": "Mon",
    "tuesday": "Tue", "tue": "Tue", "tues": "Tue", "tuesdays": "Tue",
    "wednesday": "Wed", "wed": "Wed", "weds": "Wed", "wednesdays": "Wed",
    "thursday": "Thu", "thu": "Thu", "thur": "Thu", "thurs": "Thu", "thursdays": "Thu",
    "friday": "Fri", "fri": "Fri", "fridays": "Fri",
    "saturday": "Sat", "sat": "Sat", "saturdays": "Sat",
    "sunday": "Sun", "sun": "Sun", "sundays": "Sun",
    "weekday": WEEKDAY, "weekdays": WEEKDAY, "workday": WEEKDAY, "workdays": WEEKDAY,
    "weekend": WEEKEND, "weekends": WEEKEND,
}
_RELATIVE_DAYS = {"today": 0, "tonight": 0, "now": 0, "tomorrow": 1, "tmrw": 1,
                  "tomorrows": 1, "yesterday": -1}


def parse_day(text: str, today: date) -> str | None:
    """'Fri', 'Weekday', 'Weekend' ... or None when `text` names no day."""
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return None
    if re.search(r"\b(working|work|week)\s+days?\b|\bmon(day)?\s*(-|to)\s*fri(day)?\b", cleaned):
        return WEEKDAY
    if re.search(r"\bsat(urday)?\s*(-|and|to)\s*sun(day)?\b", cleaned):
        return WEEKEND
    words = re.findall(r"[a-z]+", cleaned)
    for word in words:
        if word in _DAY_WORDS:
            return _DAY_WORDS[word]
    for word in words:
        if word in _RELATIVE_DAYS:
            return DAYS[(today + timedelta(days=_RELATIVE_DAYS[word])).weekday()]
    match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", cleaned)
    if match:
        try:
            return DAYS[date(*map(int, match.groups())).weekday()]
        except ValueError:
            return "?"
    match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", cleaned)
    if match:
        day_n, month_n, year_s = match.groups()
        year = int(year_s) if year_s else today.year
        year = year + 2000 if year < 100 else year
        try:
            return DAYS[date(year, int(month_n), int(day_n)).weekday()]
        except ValueError:
            return "?"
    words_only = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", cleaned.replace(",", " "))
    for fmt in ("%d %B", "%d %b", "%B %d", "%b %d", "%d %B %Y", "%d %b %Y", "%B %d %Y"):
        try:
            parsed = datetime.strptime(" ".join(words_only.split()), fmt)
        except ValueError:
            continue
        year = parsed.year if "%Y" in fmt else today.year
        try:
            return DAYS[date(year, parsed.month, parsed.day).weekday()]
        except ValueError:
            return "?"
    return "?"


_NOW_WORDS = {"now", "right now", "currently", "at the moment", "just now", "asap", "this minute"}
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def _hour_24(hour: int, minute: int, suffix: str, leading_zero: bool) -> int | None:
    if suffix in ("am", "pm"):
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    elif not leading_zero and 1 <= hour <= 6:
        hour += 12          # "at 5" in a travel question means 5pm
    if not (0 <= hour <= 24 and 0 <= minute < 60) or (hour == 24 and minute):
        return None
    return (hour * 60 + minute) % DAY_END


def parse_time(text: str) -> int | str | None:
    """Minutes after midnight, "now", None for no time, or "?" if unreadable."""
    cleaned = (text or "").strip().lower()
    cleaned = re.sub(r"\b(at|around|about|approx|approximately|roughly|by|on|ish)\b|[~?]", " ", cleaned)
    cleaned = cleaned.replace("o'clock", "").replace("oclock", "").replace("a.m.", "am").replace("p.m.", "pm")
    cleaned = " ".join(cleaned.replace("-ish", "").split())
    if not cleaned:
        return None
    if cleaned in _NOW_WORDS:
        return "now"
    if cleaned in ("noon", "midday", "12 noon", "lunchtime noon"):
        return 12 * 60
    if cleaned in ("midnight", "12 midnight"):
        return 0
    for word, number in _NUMBER_WORDS.items():
        cleaned = re.sub(rf"\b{word}\b", str(number), cleaned)
    match = re.fullmatch(r"(half past|quarter past|quarter to|half)\s+(\d{1,2})\s*(am|pm)?", cleaned)
    if match:
        phrase, hour_s, suffix = match.groups()
        hour = int(hour_s)
        offset = {"half past": 30, "half": 30, "quarter past": 15, "quarter to": -15}[phrase]
        base = _hour_24(hour, 0, suffix or "", False)
        return None if base is None else (base + offset) % DAY_END
    match = re.fullmatch(r"(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm)?", cleaned)
    if match:
        hour_s, minute_s, suffix = match.groups()
        result = _hour_24(int(hour_s), int(minute_s or 0), suffix or "", hour_s.startswith("0") or bool(minute_s and int(hour_s) >= 13))
        return "?" if result is None else result
    match = re.fullmatch(r"(\d{2})(\d{2})(?:\s*hrs?)?", cleaned)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour < 24 and minute < 60:
            return hour * 60 + minute
    return "?"


#: Named parts of the day, as (start, end) minutes. "am"/"pm" peaks and
#: "rush hour" come from TfL's own peak bands for the station and day.
_WINDOWS = [
    ("early morning", (5 * 60, 7 * 60)),
    ("first thing", (5 * 60, 7 * 60)),
    ("morning rush", "am"), ("morning peak", "am"), ("am peak", "am"),
    ("morning commute", "am"), ("before work", "am"),
    ("evening rush", "pm"), ("evening peak", "pm"), ("pm peak", "pm"),
    ("evening commute", "pm"), ("after work", "pm"), ("home time", "pm"),
    ("rush hour", "rush"), ("rush", "rush"), ("peak", "rush"), ("commute", "rush"),
    ("off peak", (9 * 60 + 30, 16 * 60)), ("off-peak", (9 * 60 + 30, 16 * 60)),
    ("lunchtime", (11 * 60 + 30, 14 * 60 + 30)), ("lunch", (11 * 60 + 30, 14 * 60 + 30)),
    ("midday", (11 * 60 + 30, 14 * 60 + 30)),
    ("working hours", (9 * 60, 17 * 60)), ("office hours", (9 * 60, 17 * 60)),
    ("morning", (7 * 60, 12 * 60)), ("am", (7 * 60, 12 * 60)),
    ("afternoon", (12 * 60, 17 * 60)),
    ("late night", (21 * 60, DAY_END)), ("tonight", (17 * 60, DAY_END)),
    ("evening", (17 * 60, 21 * 60)), ("pm", (12 * 60, DAY_END)),
    ("night", (21 * 60, DAY_END)), ("late", (21 * 60, DAY_END)),
    ("daytime", DEFAULT_WINDOW), ("all day", DEFAULT_WINDOW), ("day", DEFAULT_WINDOW),
]


def _band(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", text or "")
    if not match:
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    return h1 * 60 + m1, h2 * 60 + m2


def parse_window(text: str) -> tuple[str, object] | None:
    """(label, (start, end) | "am" | "pm" | "rush") or None."""
    cleaned = " ".join((text or "").strip().lower().split())
    if not cleaned:
        return None
    for prefix, start_end in (("after", "after"), ("from", "after"), ("before", "before"),
                              ("until", "before"), ("till", "before")):
        match = re.fullmatch(rf"{prefix}\s+(.+)", cleaned)
        if match and not re.search(r"\b(to|and|-|until|till)\b", match.group(1)):
            moment = parse_time(match.group(1))
            if isinstance(moment, int):
                span = (moment, DAY_END) if start_end == "after" else (5 * 60, moment)
                return cleaned, span
    match = re.fullmatch(r"(?:between|from)?\s*(.+?)\s*(?:-|to|and|until|till)\s*(.+)", cleaned)
    if match:
        end_text = match.group(2)
        end = parse_time(end_text)
        start = parse_time(match.group(1))
        if isinstance(start, int) and isinstance(end, int):
            suffix_end = re.search(r"(am|pm)\s*$", end_text)
            if suffix_end and not re.search(r"(am|pm)", match.group(1)):
                start_raw = parse_time(match.group(1) + suffix_end.group(1))
                if isinstance(start_raw, int) and start_raw < end:
                    start = start_raw
            if end == 0:
                end = DAY_END
            if start < end:
                return cleaned, (start, end)
    for word, span in _WINDOWS:
        if re.search(rf"\b{re.escape(word)}\b", cleaned):
            return word, span
    return None


def hhmm(minutes: int) -> str:
    minutes %= DAY_END
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def level_of(pct: int) -> str:
    if pct < QUIET_BELOW:
        return "quiet"
    if pct < MODERATE_BELOW:
        return "moderate"
    if pct < BUSY_BELOW:
        return "busy"
    return "very busy"


# ── The app ─────────────────────────────────────────────────────────────────


class App:
    """How busy a tube station is, from TfL's bundled crowding figures."""

    def __init__(self, data: dict | None = None) -> None:
        self._manifest = validate_manifest(load_manifest_file(MANIFEST_PATH))
        self.data = DATA if data is None else data
        self.index = StationIndex(self.data)

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def handle(self, call: Call) -> Result:
        if call.outcome_id not in OUTCOMES:
            raise AgentAppError(f"this app cannot do {call.outcome_id!r}")
        try:
            return self._answer(call)
        except AgentAppError:
            raise
        except Exception as exc:  # noqa: BLE001 - never a bare crash in front of a person
            raise QuietTimeError(f"unexpected {type(exc).__name__}: {exc}") from exc

    # ── the steps ──

    def _answer(self, call: Call) -> Result:
        inputs = call.inputs or {}
        station_text = (inputs.get("station") or "").strip()
        day_text = (inputs.get("day") or "").strip()
        time_text = (inputs.get("time") or "").strip()
        window_text = (inputs.get("window") or "").strip()

        if not self.data.get("stations"):
            raise DataUnavailable("crowding.json is missing or empty")
        if not station_text:
            raise NoStation("no station in the message")

        naptan = self._station(station_text)
        name = self.index.names[naptan]
        station = self.data["stations"].get(naptan)
        if not station or not station.get("days"):
            raise _fail(NoCrowdingData, f"no figures for {naptan}",
                        f"TfL has no crowding figures for {name}.")

        now = call.context.now.astimezone(LONDON)
        today_key = DAYS[now.weekday()]
        day_key = parse_day(day_text, now.date())
        if day_key == "?":
            raise _fail(UnreadableDay, f"unreadable day {day_text!r}",
                        f"I couldn't tell which day \"{day_text}\" is; try a day such as "
                        "Friday, or weekday or weekend.")
        if day_key is None:
            # "friday evening" may arrive in the time or window field instead.
            for other in (time_text, window_text):
                found = parse_day(other, now.date())
                if found and found != "?":
                    day_key = found
                    break
        # Whether "no time given" means "now": only for the current day.
        about_now = day_key is None or day_key == today_key
        day_key = day_key or today_key

        window = parse_window(window_text)
        moment = parse_time(time_text)
        if moment == "?":
            # "evening" or "rush hour" put in the time field is a window.
            window = window or parse_window(time_text)
            if window is None:
                raise _fail(UnreadableTime, f"unreadable time {time_text!r}",
                            f"I couldn't read the time \"{time_text}\"; try something like "
                            "5:30pm or 17:30.")
            moment = None
        if window is None and day_text:
            leftover = re.sub(r"\b(" + "|".join(_DAY_WORDS) + r")\b", " ", day_text.lower())
            window = parse_window(leftover)
            if window and window[0] == "day":
                window = None
        if moment == "now" or (moment is None and about_now and window is None
                               and call.outcome_id == "crowding.at"):
            moment = now.hour * 60 + now.minute

        values, am_band, pm_band, label = self._profile(station, day_key)
        if values is None:
            raise _fail(NoCrowdingData, f"no figures for {naptan} on {day_key}",
                        f"TfL has no crowding figures for {name} on {DAY_NAMES.get(day_key, day_key)}.")

        reference = max(_peak_raw(station), 1)
        pct = [int(round(100 * v / reference)) for v in values]

        span, window_label = self._window_span(window, moment, am_band, pm_band)
        quiet_i = self._quietest(pct, span, moment)
        busy_i = max(range(BANDS), key=lambda i: (pct[i], -i))

        data = {
            "station": name,
            "day": label,
            "am_peak": am_band or "not published",
            "pm_peak": pm_band or "not published",
            "quietest_time_in_window": hhmm(quiet_i * BAND),
            "busiest_time": hhmm(busy_i * BAND),
            "source": f"TfL open data, downloaded {self.data.get('downloaded') or 'date unknown'}",
        }
        if isinstance(moment, int):
            now_pct = pct[(moment // BAND) % BANDS]
            data.update({"time": hhmm(moment), "busyness_pct": now_pct, "level": level_of(now_pct)})

        narrative = self._narrative(call.outcome_id, data, pct, moment, window, window_label,
                                    span, quiet_i, busy_i, am_band, pm_band, label)
        return Result(data=data, narrative=narrative)

    def _station(self, text: str) -> str:
        naptan, suggestion = self.index.find(text)
        if naptan:
            return naptan
        message = f"I couldn't find a London tube station called \"{text}\""
        message += f"; did you mean {suggestion}?" if suggestion else "."
        raise _fail(UnknownStation, f"no station matches {text!r}", message)

    @staticmethod
    def _profile(station: dict, day_key: str):
        """(96 raw values, am band, pm band, day label) for a day or a group."""
        days = station.get("days", {})
        if day_key in GROUP_DAYS:
            members = [d for d in GROUP_DAYS[day_key] if d in days]
            if not members:
                return None, "", "", GROUP_LABELS[day_key]
            values = [sum(days[d][i] for d in members) / len(members) for i in range(BANDS)]

            def common(bands: dict) -> str:
                found = [bands.get(d, "") for d in members if bands.get(d)]
                return Counter(found).most_common(1)[0][0] if found else ""

            return values, common(station.get("am", {})), common(station.get("pm", {})), GROUP_LABELS[day_key]
        if day_key not in days:
            return None, "", "", DAY_NAMES.get(day_key, day_key)
        return (days[day_key], station.get("am", {}).get(day_key, ""),
                station.get("pm", {}).get(day_key, ""), DAY_NAMES[day_key])

    @staticmethod
    def _window_span(window, moment, am_band: str, pm_band: str):
        """The (start, end) minutes to look for the quietest time in, and a label."""
        if window:
            label, span = window
            if span == "am":
                band = _band(am_band) or (7 * 60, 10 * 60)
                return band, "in the am peak"
            if span == "pm":
                band = _band(pm_band) or (16 * 60, 19 * 60)
                return band, "in the pm peak"
            if span == "rush":
                start = (_band(am_band) or (7 * 60, 10 * 60))[0]
                end = (_band(pm_band) or (16 * 60, 19 * 60))[1]
                return (start, end), "around the rush hours"
            start, end = span
            return (start, end), f"between {hhmm(start)} and {hhmm(end) if end < DAY_END else '24:00'}"
        if isinstance(moment, int):
            start = max(5 * 60, moment - NEAR_TIME)
            end = min(DAY_END, moment + NEAR_TIME + BAND)
            if end - start < 4 * BAND:
                start = max(0, end - 4 * NEAR_TIME)
            return (start, end), f"between {hhmm(start)} and {hhmm(end) if end < DAY_END else '24:00'}"
        return DEFAULT_WINDOW, f"between {hhmm(DEFAULT_WINDOW[0])} and {hhmm(DEFAULT_WINDOW[1])}"

    @staticmethod
    def _quietest(pct: list[int], span: tuple[int, int], moment) -> int:
        start, end = span
        first = max(0, start // BAND)
        last = max(first + 1, min(BANDS, -(-end // BAND)))
        indexes = range(first, last)
        anchor = moment // BAND if isinstance(moment, int) else first
        return min(indexes, key=lambda i: (pct[i], abs(i - anchor), i))

    @staticmethod
    def _narrative(outcome, data, pct, moment, window, window_label, span, quiet_i, busy_i,
                   am_band, pm_band, label) -> str:
        name = data["station"]
        on_day = f"on {label}" if label in DAY_NAMES.values() else f"on {label.split(' (')[0].lower()}"
        peaks = ", ".join(p for p in (f"am peak {am_band}" if am_band else "",
                                      f"pm peak {pm_band}" if pm_band else "") if p)
        quiet_txt = f"{data['quietest_time_in_window']} ({pct[quiet_i]}%)"
        busiest_txt = f"{data['busiest_time']} ({pct[busy_i]}% of its peak)"

        rush_question = bool(window and window[1] in ("am", "pm", "rush"))
        if isinstance(moment, int) and outcome == "crowding.at":
            i = (moment // BAND) % BANDS
            level = data["level"]
            which = App._relevant_peak(moment, am_band, pm_band)
            lead = f"{name} {on_day} at {data['time']} is {level} ({data['busyness_pct']}% of its peak"
            lead += f", {which})" if which else ")"
            if level in ("busy", "very busy"):
                eased = next((j for j in range(i + 1, BANDS) if pct[j] < MODERATE_BELOW), None)
                quiet = next((j for j in range(i + 1, BANDS) if pct[j] < QUIET_BELOW), None)
                if eased is not None and quiet is not None and quiet > eased:
                    return (f"{lead}; it eases to moderate from {hhmm(eased * BAND)} and is "
                            f"quiet again from {hhmm(quiet * BAND)}.")
                if quiet is not None:
                    return f"{lead}; it is quiet again from {hhmm(quiet * BAND)}."
                if eased is not None:
                    return f"{lead}; it eases to moderate from {hhmm(eased * BAND)}."
                return f"{lead}; it stays busy until late."
            if busy_i == i:
                return f"{lead}, its busiest time of the day."
            return f"{lead}; it is busiest at {busiest_txt}."
        if rush_question:
            return (f"{name} {on_day} has its rush hours at {peaks or 'times TfL does not publish'}, "
                    f"busiest at {busiest_txt}; the quietest time {window_label} is {quiet_txt}.")
        if isinstance(moment, int):
            return (f"{name} {on_day} is quietest {window_label} at {quiet_txt}, against "
                    f"{data['busyness_pct']}% at {data['time']} ({data['level']}); it is busiest "
                    f"at {busiest_txt}.")
        return (f"{name} {on_day} is quietest {window_label} at {quiet_txt} and busiest at "
                f"{busiest_txt}{'; ' + peaks if peaks else ''}.")

    @staticmethod
    def _relevant_peak(moment: int, am_band: str, pm_band: str) -> str:
        am, pm = _band(am_band), _band(pm_band)
        if am and am[0] <= moment < am[1]:
            return f"inside the am peak {am_band}"
        if pm and pm[0] <= moment < pm[1]:
            return f"inside the pm peak {pm_band}"
        if am and moment < am[0]:
            return f"am peak {am_band}"
        if pm:
            return f"pm peak {pm_band}"
        return ""
