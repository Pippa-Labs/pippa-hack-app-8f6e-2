"""Tests for Quiet Time. Run them with `pytest`.

Keep these. Publishing runs them, and a repository whose tests fail does not
deploy — which is the fastest feedback you will get all day, because the
alternative is finding out from a user that the app answers wrongly.

`LocalRunner` is the same code the production runtime uses to call the app.
So a call that works here works deployed, and a `Result` that breaks the
manifest fails here exactly as it would in production. That is why every test
goes through the runner rather than calling `App().handle(...)` directly.

Everything runs against the bundled `crowding.json` (TfL's data, which never
changes once a station is in the file). A synthetic dataset, `App(data=...)`,
is used only for what the real file cannot produce: a station missing a day,
one TfL lists as always quiet, and no data at all.

`crowding.json` is filled in by `scripts/build_data.py`, Zone 1 first. A test
about a station that has not been downloaded yet skips with the reason, and
runs in full once it has.
"""

from __future__ import annotations

import ast
import copy
import functools
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pippa_agentapp_sdk import (
    AgentAppError,
    InputShapeViolation,
    LocalRunner,
    OutputShapeViolation,
    Result,
    UndeclaredOutcome,
)

from app import (
    App,
    DataUnavailable,
    NoCrowdingData,
    NoStation,
    UnknownStation,
    UnreadableDay,
    UnreadableTime,
)

# A fixed "now", so a test that passes today still passes next March.
# Friday 20 November 2026, 14:50 UTC, which is 14:50 in London too (GMT in
# winter). The British Summer Time test below uses its own `now`.
NOW = datetime(2026, 11, 20, 14, 50, tzinfo=timezone.utc)

AT, QUIETEST = "crowding.at", "crowding.quietest"
ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "crowding.json"
MANIFEST_FILE = ROOT / "manifest.yaml"

REQUIRED_OUTPUT = {"station", "day", "am_peak", "pm_peak", "quietest_time_in_window",
                   "busiest_time", "source"}
TIME_OUTPUT = {"time", "busyness_pct", "level"}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
WEEKEND = ("Sat", "Sun")


def call(inputs: dict, outcome: str = AT, *, explicit: bool = False, now: datetime = NOW,
         app: App | None = None):
    """Run one outcome the way the runtime does, and return the Result.

    `granted_secrets` is required even though this app never has any: the
    runner takes it on every call, so an empty dict is how you say "none".

    `explicit=True` calls the app the way Pippa does when the person picked
    it by name (`@Quiet Time ...`) instead of Pippa choosing it.
    """
    return LocalRunner(app or App()).call(
        outcome, inputs, granted_secrets={}, now=now, request_id="t1",
        explicit=explicit,
    ).result


def refusal(inputs: dict, outcome: str = AT, *, app: App | None = None) -> AgentAppError:
    """The error a call raises. `user_message` is the only part a person sees."""
    with pytest.raises(AgentAppError) as failure:
        call(inputs, outcome, app=app)
    return failure.value


# ── An independent reading of crowding.json, to check the app's answers ────


@functools.lru_cache(maxsize=1)
def _file() -> dict:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _entry(name: str) -> dict:
    for entry in _file()["stations"].values():
        if entry.get("name") == name:
            return entry
    raise LookupError(f"{name} is not in crowding.json")


def percent_profile(name: str, day: str) -> list[int]:
    """96 fifteen-minute figures for a station and a day ("Fri") or a group
    ("Weekday", "Weekend"), as a percent of the station's busiest fifteen
    minutes in the whole week: the definition the app documents."""
    entry = _entry(name)
    peak = max(v for values in entry["days"].values() for v in values)
    if day in ("Weekday", "Weekend"):
        members = [d for d in (WEEKDAYS if day == "Weekday" else WEEKEND) if d in entry["days"]]
        values = [sum(entry["days"][d][i] for d in members) / len(members) for i in range(96)]
    else:
        values = entry["days"][day]
    return [int(round(100 * v / peak)) for v in values]


def minutes(hhmm: str) -> int:
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def band(hhmm: str) -> int:
    return minutes(hhmm) // 15


def bands_in(start: int, end: int) -> range:
    """The fifteen-minute bands that start inside [start, end) minutes."""
    return range(start // 15, -(-end // 15))


def level_for(pct: int) -> str:
    if pct < 30:
        return "quiet"
    if pct < 60:
        return "moderate"
    if pct < 85:
        return "busy"
    return "very busy"


def in_the_data(station: str) -> bool:
    return any(s.get("name") == station for s in App().data["stations"].values())


def need(station: str) -> None:
    """Skip, with the reason, while `station` has not been downloaded yet."""
    if not in_the_data(station):
        pytest.skip(f"{station} is not in crowding.json yet (scripts/build_data.py is still "
                    "downloading it); this test runs in full once it is")


def assert_one_sentence(text: str, *, names: tuple[str, ...] = (), ends: tuple[str, ...] = (".",)):
    """One plain sentence: no line break, one closing mark, none inside.

    Station names such as "St. Paul's" carry a full stop of their own, so the
    names are taken out before looking for a sentence break.
    """
    assert isinstance(text, str) and text.strip(), text
    assert "\n" not in text, text
    assert text.endswith(ends), text
    body = text[:-1]
    for name in names:
        body = body.replace(name, "NAME")
    assert not re.search(r"[.!?](\s|$)", body), f"more than one sentence: {text!r}"


def assert_consistent(data: dict) -> None:
    """What every answer must satisfy, whatever was asked."""
    assert REQUIRED_OUTPUT <= set(data)
    present = TIME_OUTPUT & set(data)
    assert present in (set(), TIME_OUTPUT), f"time, busyness_pct and level come together: {data}"
    for field in ("quietest_time_in_window", "busiest_time") + (("time",) if present else ()):
        assert re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", data[field]), data
    for field in ("am_peak", "pm_peak"):
        assert re.fullmatch(r"\d\d:\d\d-\d\d:\d\d|not published", data[field]), data
    if present:
        assert isinstance(data["busyness_pct"], int) and not isinstance(data["busyness_pct"], bool)
        assert 0 <= data["busyness_pct"] <= 100
        assert data["level"] == level_for(data["busyness_pct"])


# ── The manifest ────────────────────────────────────────────────────────────


def test_the_manifest_is_valid():
    """The first thing to break, and the easiest to miss.

    `App()` validates manifest.yaml as it is constructed, so this fails the
    moment the manifest is malformed, before anything else can produce a more
    confusing error.
    """
    app = App()
    assert app.manifest.app_id.startswith("hack.")
    assert [o.id for o in app.manifest.outcomes] == ["crowding.at", "crowding.quietest"]


def test_the_summary_never_says_by_and_tfl_is_credited():
    """Pippa's badge splits on the first "by", and TfL's licence wants credit.

    So the attribution lives in the manifest's comments and `source` fields,
    never in `app.summary`. Nothing else checks either until long after a
    green build.
    """
    text = MANIFEST_FILE.read_text(encoding="utf-8")
    summary = yaml.safe_load(text)["app"]["summary"]
    assert not re.search(r"\bby\b", summary, re.IGNORECASE), summary
    assert "Powered by TfL Open Data" in text


def test_both_outcomes_take_and_return_the_same_fields():
    """The two outcomes are two ways of asking the same thing.

    Every input is optional (a missing station must reach the app, which says
    what to type, rather than failing inside the runtime), and the time
    fields are optional outputs because a question about another day has no
    time to report.
    """
    outcomes = yaml.safe_load(MANIFEST_FILE.read_text(encoding="utf-8"))["outcomes"]
    for outcome in outcomes:
        inputs = {f["name"]: f for f in outcome["inputs"]}
        assert list(inputs) == ["station", "day", "time", "window"]
        assert all(f["type"] == "string" and f.get("required") is False for f in inputs.values())
        output = {f["name"]: f for f in outcome["output"]}
        assert set(output) == REQUIRED_OUTPUT | TIME_OUTPUT
        assert {n for n, f in output.items() if f.get("required", True)} == REQUIRED_OUTPUT
        assert output["busyness_pct"]["type"] == "integer"


# ── The demo questions ──────────────────────────────────────────────────────


def test_how_busy_is_oxford_circus_at_half_five_on_a_friday():
    """"How busy is Oxford Circus at 5:30pm on a Friday?" — the headline demo.

    Oxford Circus is in the file and its figures never change, so the
    numbers are pinned: 17:30 is inside the evening peak, at 81% of the
    busiest fifteen minutes of its week.
    """
    result = call({"station": "Oxford Circus", "day": "Friday", "time": "5:30pm"})
    data = result.data
    assert data["station"] == "Oxford Circus"
    assert data["day"] == "Friday"
    assert data["time"] == "17:30"
    assert data["busyness_pct"] == 81
    assert data["level"] == "busy"
    assert data["am_peak"] == "08:15-10:15"
    assert data["pm_peak"] == "17:00-19:00"
    assert data["busiest_time"] == "18:00"
    assert_consistent(data)
    assert_one_sentence(result.narrative, names=("Oxford Circus",))
    assert "Oxford Circus" in result.narrative and "81%" in result.narrative


def test_when_is_bank_quietest_on_a_weekday_morning():
    """"When is Bank quietest on a weekday morning?"

    "weekday" averages Monday to Friday and says so in `day`; "morning" is
    07:00-12:00, so the answer must fall inside it and be the quietest band
    there. No time was asked, so none is reported.
    """
    result = call({"station": "Bank", "day": "weekday", "window": "morning"}, QUIETEST)
    data = result.data
    assert data["station"] == "Bank"
    assert data["day"] == "Weekdays (Mon-Fri average)"
    assert minutes("07:00") <= minutes(data["quietest_time_in_window"]) < minutes("12:00")
    pct = percent_profile("Bank", "Weekday")
    assert pct[band(data["quietest_time_in_window"])] == min(pct[i] for i in bands_in(7 * 60, 12 * 60))
    assert data["quietest_time_in_window"] == "11:00"
    assert data["busiest_time"] == "08:45"
    assert data["am_peak"] == "08:00-10:00"
    assert data["pm_peak"] == "17:00-19:00"
    assert not TIME_OUTPUT & set(data)
    assert_consistent(data)
    assert_one_sentence(result.narrative, names=("Bank",))
    assert "Bank" in result.narrative


def test_is_kings_cross_busy_at_8am():
    """"Is King's Cross busy at 8am?" — a nickname, and no day, so today.

    NOW is a Friday, so the figures are Friday's. King's Cross is in the
    file, so the numbers are pinned.
    """
    result = call({"station": "Kings X", "time": "8am"})
    data = result.data
    assert data["station"] == "King's Cross St. Pancras"
    assert data["day"] == "Friday"
    assert data["time"] == "08:00"
    assert data["busyness_pct"] == 43
    assert data["level"] == "moderate"
    assert data["am_peak"] == "08:15-10:15"
    assert data["pm_peak"] == "17:00-19:00"
    assert data["busiest_time"] == "17:45"
    assert_consistent(data)
    # "King's Cross St. Pancras" has a full stop of its own, which is not a
    # sentence break.
    assert_one_sentence(result.narrative, names=("King's Cross St. Pancras",))
    assert "King's Cross St. Pancras" in result.narrative


def test_best_time_to_go_through_waterloo_on_saturday():
    """"Best time to go through Waterloo on Saturday?"

    Saturday is not today, and no time was asked, so there is no time to
    report; the quietest time must lie in travelling hours (the manifest
    promises 07:00-22:00, not 03:00 when the station is shut) and be the
    quietest band of them.
    """
    need("Waterloo")
    result = call({"station": "Waterloo", "day": "Saturday"}, QUIETEST)
    data = result.data
    assert data["station"] == "Waterloo"
    assert data["day"] == "Saturday"
    assert not TIME_OUTPUT & set(data)
    quiet = minutes(data["quietest_time_in_window"])
    assert minutes("07:00") <= quiet < minutes("22:00")
    pct = percent_profile("Waterloo", "Sat")
    assert pct[band(data["quietest_time_in_window"])] == min(pct[i] for i in bands_in(7 * 60, 22 * 60))
    assert pct[band(data["busiest_time"])] == max(pct)
    assert_consistent(data)
    assert_one_sentence(result.narrative, names=("Waterloo",))
    assert "Waterloo" in result.narrative


def test_when_is_the_rush_hour_at_canary_wharf():
    """"When's the rush hour at Canary Wharf?" — TfL's own peak bands.

    No day, so today (Friday). The rush hours come from TfL's published
    peaks, and the quietest time asked about lies between the start of the
    morning peak and the end of the evening one.
    """
    need("Canary Wharf")
    result = call({"station": "Canary Wharf", "window": "rush hour"}, QUIETEST)
    data = result.data
    assert data["station"] == "Canary Wharf"
    assert data["day"] == "Friday"
    assert not TIME_OUTPUT & set(data)
    assert_consistent(data)
    assert re.fullmatch(r"\d\d:\d\d-\d\d:\d\d", data["am_peak"])
    assert re.fullmatch(r"\d\d:\d\d-\d\d:\d\d", data["pm_peak"])
    start, end = minutes(data["am_peak"][:5]), minutes(data["pm_peak"][-5:])
    assert start <= minutes(data["quietest_time_in_window"]) < end
    pct = percent_profile("Canary Wharf", "Fri")
    assert pct[band(data["quietest_time_in_window"])] == min(pct[i] for i in bands_in(start, end))
    assert_one_sentence(result.narrative, names=("Canary Wharf",))
    assert "Canary Wharf" in result.narrative
    assert data["am_peak"] in result.narrative and data["pm_peak"] in result.narrative


# ── Station names as people type them ───────────────────────────────────────


@pytest.mark.parametrize("typed, station", [
    ("Kings X", "King's Cross St. Pancras"),
    ("St Pauls", "St. Paul's"),
    ("Earl\u2019s Court", "Earl's Court"),          # a curly apostrophe, as phones type it
    ("Elephant and Castle", "Elephant & Castle"),
    ("Elephant & Castle", "Elephant & Castle"),
    ("Picadilly Circus", "Piccadilly Circus"),      # a typo
    ("  oxford circus  ", "Oxford Circus"),
])
def test_station_names_as_people_type_them(typed, station):
    """Nobody types "King's Cross St. Pancras". Every spelling here is one
    Pippa will pass through as written, and each must reach the right
    station rather than a refusal or, worse, a different station."""
    data = call({"station": typed}).data
    assert data["station"] == station
    assert_consistent(data)


@pytest.mark.parametrize("typed, station", [
    ("Tottenham Ct Rd", "Tottenham Court Road"),
    ("Heathrow T5", "Heathrow Terminal 5"),
])
def test_abbreviated_names_once_downloaded(typed, station):
    """Abbreviations ("Ct Rd", "T5") reach the station TfL means.

    Until the station is downloaded these skip: a near neighbour (Heathrow
    Terminals 2 & 3 for "T5") would otherwise answer in its place.
    """
    need(station)
    data = call({"station": typed}).data
    assert data["station"] == station
    assert_consistent(data)


def test_an_unknown_station_is_named_in_the_refusal():
    """"Narnia" is not a tube station. The person sees the refusal word for
    word, so it names what they typed, which tells them what to retype."""
    error = refusal({"station": "Narnia"})
    assert isinstance(error, UnknownStation)
    assert "Narnia" in error.user_message


def test_a_near_miss_suggests_the_station():
    """"Bnak" is too far from "Bank" to answer for it outright, but close
    enough to ask: the app suggests the station rather than guessing."""
    error = refusal({"station": "Bnak"})
    assert isinstance(error, UnknownStation)
    assert "Bnak" in error.user_message
    assert "did you mean Bank?" in error.user_message


@pytest.mark.parametrize("inputs", [{}, {"station": ""}, {"station": "   "}],
                         ids=["absent", "empty", "blank"])
def test_no_station_asks_for_one(inputs):
    """The extractor returns an empty string, or nothing, when no station was
    named. Both must ask for one rather than pick a station."""
    error = refusal(inputs)
    assert isinstance(error, NoStation)
    assert "station" in error.user_message.lower()


def test_a_station_tfl_has_no_figures_for():
    """TfL publishes no crowding figures for some stations (Monument among
    them). The refusal names the station, so the person knows it is the
    station, not their spelling."""
    monument = next((n for n, name in App().data["no_data"].items() if name == "Monument"), None)
    if monument is None:
        pytest.skip("Monument is not listed under no_data in crowding.json")
    error = refusal({"station": "Monument", "day": "Friday", "time": "8am"})
    assert isinstance(error, NoCrowdingData)
    if monument in (App().data.get("always_quiet") or []):
        assert error.user_message == ("TfL has no crowding figures for Monument, "
                                      "which it lists as always quiet.")
    else:
        assert error.user_message == "TfL has no crowding figures for Monument."


# ── Synthetic data, for what the real file cannot produce ───────────────────


def _dataset_with(*, weekend: bool = True, no_data: dict | None = None,
                  always_quiet: list | None = None) -> dict:
    """A copy of the real Oxford Circus entry, trimmed to what a test needs."""
    naptan, entry = next((k, v) for k, v in _file()["stations"].items()
                         if v["name"] == "Oxford Circus")
    entry = copy.deepcopy(entry)
    if not weekend:
        for key in ("days", "am", "pm"):
            for day in WEEKEND:
                entry.get(key, {}).pop(day, None)
    data = {"source": "TfL open data", "downloaded": "2026-11-20",
            "stations": {naptan: entry}, "no_data": dict(no_data or {})}
    if always_quiet is not None:
        data["always_quiet"] = list(always_quiet)
    return data


def test_a_station_missing_a_day_says_which_day():
    """A station with no Saturday figures refuses Saturday by name, and
    still answers for the days it has."""
    app = App(data=_dataset_with(weekend=False))
    error = refusal({"station": "Oxford Circus", "day": "Saturday"}, app=app)
    assert isinstance(error, NoCrowdingData)
    assert error.user_message == "TfL has no crowding figures for Oxford Circus on Saturday."
    assert call({"station": "Oxford Circus", "day": "Friday"}, app=app).data["day"] == "Friday"


def test_a_station_tfl_lists_as_always_quiet_says_so():
    """TfL marks some stations it has no figures for as always quiet. That
    is an answer of sorts, and the refusal passes it on."""
    app = App(data=_dataset_with(no_data={"940GZZLURVY": "Roding Valley"},
                                 always_quiet=["940GZZLURVY"]))
    error = refusal({"station": "Roding Valley"}, app=app)
    assert isinstance(error, NoCrowdingData)
    assert error.user_message == ("TfL has no crowding figures for Roding Valley, "
                                  "which it lists as always quiet.")


def test_no_data_at_all_is_refused_plainly():
    """If crowding.json were missing from the package, every question must
    get a plain refusal, not a crash or an empty answer."""
    app = App(data={"source": "TfL open data", "downloaded": "", "stations": {}, "no_data": {}})
    error = refusal({"station": "Oxford Circus"}, app=app)
    assert isinstance(error, DataUnavailable)


# ── Refusals people can read ────────────────────────────────────────────────


def test_an_unreadable_day_is_refused():
    """"blursday" is not a day. Guessing one would answer a question nobody
    asked; the refusal says what was not understood."""
    error = refusal({"station": "Oxford Circus", "day": "blursday"})
    assert isinstance(error, UnreadableDay)
    assert "blursday" in error.user_message
    assert_one_sentence(error.user_message)


@pytest.mark.parametrize("typed", ["teatime-ish", "25:00", "13pm"])
def test_an_unreadable_time_is_refused(typed):
    """A time that is not a time, or not a real one, is refused rather than
    rounded to something plausible."""
    error = refusal({"station": "Oxford Circus", "time": typed})
    assert isinstance(error, UnreadableTime)
    assert typed in error.user_message
    assert_one_sentence(error.user_message, names=(typed,))


@pytest.mark.parametrize("inputs, app_data", [
    ({"station": ""}, None),
    ({"station": "Narnia"}, None),
    ({"station": "Bnak"}, None),
    ({"station": "Monument"}, None),
    ({"station": "Oxford Circus", "day": "blursday"}, None),
    ({"station": "Oxford Circus", "time": "teatime-ish"}, None),
    ({"station": "Oxford Circus"}, "empty"),
], ids=["NoStation", "UnknownStation", "UnknownStation-suggestion", "NoCrowdingData",
        "UnreadableDay", "UnreadableTime", "DataUnavailable"])
def test_every_refusal_is_one_sentence(inputs, app_data):
    """Pippa shows a refusal as "Quiet Time couldn't answer: <user_message>".

    One sentence reads as one; two read as a broken reply. A suggestion may
    end in a question mark.
    """
    app = App(data={"stations": {}, "no_data": {}, "downloaded": ""}) if app_data else None
    error = refusal(inputs, app=app)
    assert error.user_message != AgentAppError.user_message
    assert_one_sentence(error.user_message, ends=(".", "?"),
                        names=tuple(v for v in inputs.values() if v))


# ── Days ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("typed, day", [
    ("weekend", "Weekend (Sat-Sun average)"),
    ("tomorrow", "Saturday"),          # NOW is a Friday
    ("2026-11-24", "Tuesday"),         # an ISO date
    ("fri", "Friday"),
    ("Sat", "Saturday"),
    ("weekdays", "Weekdays (Mon-Fri average)"),
])
def test_days_as_people_write_them(typed, day):
    """The day field arrives as the person wrote it; each of these must land
    on the right day of TfL's week."""
    data = call({"station": "Oxford Circus", "day": typed}, QUIETEST).data
    assert data["day"] == day
    assert_consistent(data)


@pytest.mark.parametrize("typed", ["Monday", "tomorrow", "weekend", "2026-11-24", "2026-12-04"])
def test_another_day_without_a_time_reports_no_time(typed):
    """"How busy is Oxford Circus on Monday?" asked on a Friday has no "now".

    The manifest promises the time fields are left out when the question
    names a day other than the current one and no time. 2026-12-04 is a
    Friday, but two weeks away: not today either.
    """
    data = call({"station": "Oxford Circus", "day": typed}).data
    assert not TIME_OUTPUT & set(data), data
    assert_consistent(data)


@pytest.mark.parametrize("typed", ["", "today"])
def test_no_day_and_no_time_means_right_now(typed):
    """"How busy is Oxford Circus?" means now: 14:50 on Friday, in London."""
    data = call({"station": "Oxford Circus", "day": typed}).data
    assert data["day"] == "Friday"
    assert data["time"] == "14:50"
    assert data["busyness_pct"] == percent_profile("Oxford Circus", "Fri")[band("14:50")]
    assert_consistent(data)


def test_now_is_london_time_in_summer_too():
    """In British Summer Time, London is an hour ahead of UTC.

    13:50 UTC on a July Friday is 14:50 in London, and 23:30 UTC that night
    is already 00:30 on Saturday. An app that read the clock in UTC would
    report the wrong hour, and on the second call the wrong day.
    """
    afternoon = call({"station": "Oxford Circus"},
                     now=datetime(2026, 7, 17, 13, 50, tzinfo=timezone.utc)).data
    assert (afternoon["day"], afternoon["time"]) == ("Friday", "14:50")
    night = call({"station": "Oxford Circus"},
                 now=datetime(2026, 7, 17, 23, 30, tzinfo=timezone.utc)).data
    assert (night["day"], night["time"]) == ("Saturday", "00:30")


# ── Times ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("typed, clock", [
    ("17:30", "17:30"),
    ("5:30pm", "17:30"),
    ("half five", "17:30"),      # "five" at travelling time means the evening
    ("noon", "12:00"),
    ("8am", "08:00"),
    ("now", "14:50"),
])
def test_times_as_people_write_them(typed, clock):
    """Each spelling of a time must reach the right fifteen minutes, and the
    busyness reported must be that band's figure from the file."""
    data = call({"station": "Oxford Circus", "time": typed}).data
    assert data["time"] == clock
    assert data["busyness_pct"] == percent_profile("Oxford Circus", "Fri")[band(clock)]
    assert_consistent(data)


def test_a_day_written_into_the_time_field_is_honoured():
    """"5:30pm on Saturday" sometimes arrives whole in the time field. The
    time and the day must both be read from it, not just one."""
    data = call({"station": "Oxford Circus", "time": "5:30pm on Saturday"}).data
    assert data["day"] == "Saturday"
    assert data["time"] == "17:30"
    assert data["busyness_pct"] == percent_profile("Oxford Circus", "Sat")[band("17:30")]


# ── Windows ─────────────────────────────────────────────────────────────────


def _rush(data):
    return minutes(data["am_peak"][:5]), minutes(data["pm_peak"][-5:])


@pytest.mark.parametrize("window, span", [
    ("rush hour", _rush),                       # from the am peak's start to the pm peak's end
    ("after 6pm", lambda data: (18 * 60, 24 * 60)),
    ("4-7pm", lambda data: (16 * 60, 19 * 60)),
    ("morning", lambda data: (7 * 60, 12 * 60)),
    ("evening", lambda data: (17 * 60, 21 * 60)),
])
def test_the_quietest_time_falls_inside_the_window(window, span):
    """"When is Oxford Circus quietest after 6pm?" must not answer 06:00.

    Whatever window the person named, the answer lies inside it and is the
    quietest band there.
    """
    data = call({"station": "Oxford Circus", "window": window}, QUIETEST).data
    start, end = span(data)
    quiet = data["quietest_time_in_window"]
    assert start <= minutes(quiet) < end, (window, quiet)
    pct = percent_profile("Oxford Circus", "Fri")
    assert pct[band(quiet)] == min(pct[i] for i in bands_in(start, end))
    assert_consistent(data)


# ── What holds for every answer ─────────────────────────────────────────────


@pytest.mark.parametrize("station, day, key", [
    ("Oxford Circus", "Friday", "Fri"),
    ("Bank", "weekday", "Weekday"),
    ("King's Cross St. Pancras", "Saturday", "Sat"),
    ("Covent Garden", "Sunday", "Sun"),
    ("Euston", "Monday", "Mon"),
    ("Piccadilly Circus", "weekend", "Weekend"),
    ("St. Paul's", "Wednesday", "Wed"),
])
def test_busiest_is_the_busiest_and_quietest_the_quietest(station, day, key):
    """The two times the app reports must be true of TfL's figures.

    `busiest_time` is the busiest band of the whole day; with no window or
    time asked, `quietest_time_in_window` is the quietest band of travelling
    hours, 07:00-22:00 as the manifest promises.
    """
    result = call({"station": station, "day": day}, QUIETEST)
    data = result.data
    pct = percent_profile(station, key)
    assert pct[band(data["busiest_time"])] == max(pct)
    quiet = data["quietest_time_in_window"]
    assert minutes("07:00") <= minutes(quiet) < minutes("22:00")
    assert pct[band(quiet)] == min(pct[i] for i in bands_in(7 * 60, 22 * 60))
    assert_consistent(data)
    assert_one_sentence(result.narrative, names=(station,))
    assert station in result.narrative


@pytest.mark.parametrize("inputs, outcome", [
    ({"station": "Oxford Circus", "day": "Friday", "time": "5:30pm"}, AT),
    ({"station": "Bank", "day": "Friday", "time": "8am"}, AT),
    ({"station": "Euston", "day": "Friday", "time": "17:30"}, AT),
    ({"station": "St Pauls", "time": "9am"}, AT),
    ({"station": "Earl\u2019s Court", "time": "now"}, AT),
    ({"station": "Kings X", "time": "18:00"}, QUIETEST),
    ({"station": "Covent Garden", "window": "rush hour", "day": "Saturday"}, QUIETEST),
    ({"station": "Piccadilly Circus", "window": "lunchtime"}, QUIETEST),
])
def test_level_and_narrative_agree_with_the_figures(inputs, outcome):
    """`level` is a word for `busyness_pct` (quiet below 30, moderate below
    60, busy below 85, very busy above), the figure is the file's, the
    busiest time is no quieter than the time asked about, and the narrative
    is one sentence naming the station."""
    result = call(inputs, outcome)
    data = result.data
    assert_consistent(data)
    if "time" in data:
        key = {"Friday": "Fri", "Saturday": "Sat"}[data["day"]]
        pct = percent_profile(data["station"], key)
        assert data["busyness_pct"] == pct[band(data["time"])]
        assert pct[band(data["busiest_time"])] >= data["busyness_pct"]
        assert f"{data['busyness_pct']}%" in result.narrative
    assert_one_sentence(result.narrative, names=(data["station"],))
    assert data["station"] in result.narrative


def test_both_outcomes_agree_on_the_facts():
    """Asked the same thing, the two outcomes report the same station, day,
    peaks and busiest time: only what they put first differs."""
    inputs = {"station": "Oxford Circus", "day": "Friday", "time": "5:30pm"}
    at, quietest = call(inputs, AT).data, call(inputs, QUIETEST).data
    for field in ("station", "day", "am_peak", "pm_peak", "busiest_time", "source",
                  "time", "busyness_pct", "level"):
        assert at[field] == quietest[field], field


def test_the_source_says_where_and_when():
    """Every answer carries TfL's credit and the download date, so a figure
    is never shown without saying where it came from."""
    data = call({"station": "Oxford Circus"}).data
    assert re.fullmatch(r"TfL open data, downloaded \d{4}-\d{2}-\d{2}", data["source"])


def test_picked_by_name_it_answers_and_refuses_as_usual():
    """`@Quiet Time ...` in the chat: the person picked this app themselves.

    Then it is called whatever the words, with `call.context.explicit` True.
    The right response is the ordinary one: the same answer, and a refusal
    only for a missing or unreadable input.
    """
    for inputs, outcome in (
        ({"station": "Oxford Circus", "day": "Friday", "time": "5:30pm"}, AT),
        ({"station": "Bank", "day": "weekday", "window": "morning"}, QUIETEST),
    ):
        picked = call(inputs, outcome, explicit=True)
        assert picked.data == call(inputs, outcome).data
        assert picked.narrative == call(inputs, outcome).narrative
    with pytest.raises(NoStation):
        call({"station": ""}, explicit=True)


# ── The data file and the network ───────────────────────────────────────────


def test_the_app_never_touches_the_network():
    """TfL answers 403 to Pippa's servers, so the app works from the bundled
    file alone. No network module is imported, so none can be called."""
    import app as app_module

    tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        for banned in ("urllib", "http", "socket", "requests"):
            assert name != banned and not name.startswith(banned + "."), name
    assert "urlopen" not in Path(app_module.__file__).read_text(encoding="utf-8")


def test_the_data_file_is_small_and_well_formed():
    """The package must stay under 3 MB, and a malformed day would crash a
    question about it (or quietly answer with the wrong band)."""
    assert DATA_FILE.stat().st_size < 1_500_000
    data = _file()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["downloaded"])
    assert data["stations"]
    for naptan, entry in data["stations"].items():
        assert isinstance(entry.get("name"), str) and entry["name"], naptan
        assert entry["days"], naptan
        for day, values in entry["days"].items():
            assert day in WEEKDAYS + WEEKEND, (naptan, day)
            assert len(values) == 96, (naptan, day)
            assert all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                       for v in values), (naptan, day)


# ── The contract ────────────────────────────────────────────────────────────


def test_the_runner_enforces_the_manifest(monkeypatch):
    """Proof that the contract is real, and the reason to test through it.

    An app returning a field it did not declare is refused, exactly as it
    would be after deploying.
    """
    monkeypatch.setattr(App, "handle", lambda self, c: Result(data={"surprise": "hello"}))
    with pytest.raises(OutputShapeViolation):
        call({"station": "Oxford Circus"})


def test_the_runtime_refuses_what_the_manifest_does_not_declare():
    """An outcome the manifest does not list never runs, and an input of the
    wrong type never reaches the app."""
    with pytest.raises((UndeclaredOutcome, AgentAppError)):
        call({"station": "Oxford Circus"}, "crowding.busiest")
    with pytest.raises(InputShapeViolation):
        call({"station": 42})
