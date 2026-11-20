# Quiet Time — build report

**What it is.** A Pippa Agent App that answers: *how busy is this tube station
at a given time and day, compared with normal, and when is it quietest or
busiest?* All figures come from TfL's open crowding data (Powered by TfL Open
Data), downloaded on this Mac by `scripts/build_data.py` into `crowding.json`
beside `app.py`. The app never makes a network call.

- Repository: `Pippa-Labs/pippa-hack-app-8f6e-2`, app id `hack.app_8f6e_2`, version 0.1.0
- Author (pen name): `poppynoteaming`
- Outcomes: `crowding.at` (how busy at a time) and `crowding.quietest`
  (quietest / busiest / rush hour). Both take `station`, `day`, `time`,
  `window` and return the same shape.

<!-- BUILD-RESULT -->

## Five demo questions, with the exact replies

Run through `LocalRunner` (the local SDK stand-in, see "How it was checked")
with `now` fixed at Friday 20 November 2026, 14:50 London time. The inputs
are what Pippa's extractor is expected to pull out of each question.

### 1. "How busy is Oxford Circus at 5:30pm on a Friday?"

Outcome `crowding.at`, inputs `{"station": "Oxford Circus", "day": "Friday", "time": "5:30pm", "window": ""}`, asked at Friday 20 November 2026 14:50 London.

```json
{
  "station": "Oxford Circus",
  "day": "Friday",
  "time": "17:30",
  "busyness_pct": 81,
  "level": "busy",
  "am_peak": "08:15-10:15",
  "pm_peak": "17:00-19:00",
  "quietest_time_in_window": "15:30",
  "busiest_time": "18:00",
  "source": "TfL open data, downloaded 2026-11-20"
}
```

> Oxford Circus on Friday at 17:30 is busy (81% of its peak, inside the pm peak 17:00-19:00); it eases to moderate from 19:45 and is quiet again from 23:15.

### 2. "When is Bank quietest on a weekday morning?"

Outcome `crowding.quietest`, inputs `{"station": "Bank", "day": "weekday", "time": "", "window": "morning"}`, asked at Friday 20 November 2026 14:50 London.

```json
{
  "station": "Bank",
  "day": "Weekdays (Mon-Fri average)",
  "am_peak": "08:00-10:00",
  "pm_peak": "17:00-19:00",
  "quietest_time_in_window": "11:00",
  "busiest_time": "08:45",
  "source": "TfL open data, downloaded 2026-11-20"
}
```

> Bank on weekdays is quietest between 07:00 and 12:00 at 11:00 (16%) and busiest at 08:45 (80% of its peak); am peak 08:00-10:00, pm peak 17:00-19:00.

### 3. "Is King's Cross busy at 8am?"

Outcome `crowding.at`, inputs `{"station": "King's Cross", "day": "", "time": "8am", "window": ""}`, asked at Friday 20 November 2026 14:50 London.

```json
{
  "station": "King's Cross St. Pancras",
  "day": "Friday",
  "time": "08:00",
  "busyness_pct": 43,
  "level": "moderate",
  "am_peak": "08:15-10:15",
  "pm_peak": "17:00-19:00",
  "quietest_time_in_window": "06:00",
  "busiest_time": "17:45",
  "source": "TfL open data, downloaded 2026-11-20"
}
```

> King's Cross St. Pancras on Friday at 08:00 is moderate (43% of its peak, am peak 08:15-10:15); it is busiest at 17:45 (80% of its peak).

### 4. "Best time to go through Waterloo on Saturday?"

Outcome `crowding.quietest`, inputs `{"station": "Waterloo", "day": "Saturday", "time": "", "window": ""}`, asked at Friday 20 November 2026 14:50 London.

```json
{
  "station": "Waterloo",
  "day": "Saturday",
  "am_peak": "11:45-13:45",
  "pm_peak": "17:15-19:15",
  "quietest_time_in_window": "07:00",
  "busiest_time": "17:45",
  "source": "TfL open data, downloaded 2026-11-20"
}
```

> Waterloo on Saturday is quietest between 07:00 and 22:00 at 07:00 (10%) and busiest at 17:45 (88% of its peak); am peak 11:45-13:45, pm peak 17:15-19:15.

### 5. "When's the rush hour at Canary Wharf?"

Outcome `crowding.quietest`, inputs `{"station": "Canary Wharf", "day": "", "time": "", "window": "rush hour"}`, asked at Friday 20 November 2026 14:50 London.

```json
{
  "station": "Canary Wharf",
  "day": "Friday",
  "am_peak": "08:00-10:00",
  "pm_peak": "17:00-19:00",
  "quietest_time_in_window": "10:45",
  "busiest_time": "17:30",
  "source": "TfL open data, downloaded 2026-11-20"
}
```

> Canary Wharf on Friday has its rush hours at am peak 08:00-10:00, pm peak 17:00-19:00, busiest at 17:30 (64% of its peak); the quietest time between the peaks is 10:45 (21%).

### More replies: fuzzy names and failures

### 1. "How busy is Kings X right now?"

Outcome `crowding.at`, inputs `{"station": "Kings X", "time": "now"}`, asked at Friday 20 November 2026 14:50 London.

```json
{
  "station": "King's Cross St. Pancras",
  "day": "Friday",
  "time": "14:50",
  "busyness_pct": 50,
  "level": "moderate",
  "am_peak": "08:15-10:15",
  "pm_peak": "17:00-19:00",
  "quietest_time_in_window": "12:45",
  "busiest_time": "17:45",
  "source": "TfL open data, downloaded 2026-11-20"
}
```

> King's Cross St. Pancras on Friday at 14:50 is moderate (50% of its peak, pm peak 17:00-19:00); it is busiest at 17:45 (80% of its peak).

### 2. "Is Tottenham Ct Rd packed at half five?"

Outcome `crowding.at`, inputs `{"station": "Tottenham Ct Rd", "time": "half five"}`, asked at Friday 20 November 2026 14:50 London.

```json
{
  "station": "Tottenham Court Road",
  "day": "Friday",
  "time": "17:30",
  "busyness_pct": 79,
  "level": "busy",
  "am_peak": "08:15-10:15",
  "pm_peak": "17:00-19:00",
  "quietest_time_in_window": "15:30",
  "busiest_time": "17:45",
  "source": "TfL open data, downloaded 2026-11-20"
}
```

> Tottenham Court Road on Friday at 17:30 is busy (79% of its peak, inside the pm peak 17:00-19:00); it eases to moderate from 19:45.

### 3. "Heathrow T5 at 7am tomorrow?"

Outcome `crowding.at`, inputs `{"station": "Heathrow T5", "day": "tomorrow", "time": "7am"}`, asked at Friday 20 November 2026 14:50 London.

Refused (UnknownStation): 

> I couldn't find a London tube station called "Heathrow T5".

### 4. "How busy is Monument at 9am?"

Outcome `crowding.at`, inputs `{"station": "Monument", "time": "9am"}`, asked at Friday 20 November 2026 14:50 London.

Refused (NoCrowdingData): 

> TfL has no crowding figures for Monument.

### 5. "How busy is Narnia at 9am?"

Outcome `crowding.at`, inputs `{"station": "Narnia", "time": "9am"}`, asked at Friday 20 November 2026 14:50 London.

Refused (UnknownStation): 

> I couldn't find a London tube station called "Narnia".

## Routing check

`python scripts/routing_check.py` (run locally against the SDK stand-in;
the build runs it again with the real SDK after a passed check):

```
Will Pippa choose your app?

  OK  summary leads with something specific (109 chars seen)
       chooser sees: 'How busy a London tube station is at a time and day versus its usual peak, and when it is quietest or busiest'
  OK  crowding.at: 8 example phrases
  OK  crowding.quietest: 8 example phrases
  OK  crowding.at and crowding.quietest are distinct

Nothing here will stop your app being chosen.
```

Read by hand as well (CLAUDE.md step 3, which the script cannot check):
each promise in the summary has its own phrases. *How busy at a time and
day* → `how busy is the tube station at`, `is the station busy at 8am`;
*when it is quietest* → `when is the station quietest`, `best time to go
through the tube station`; *busiest / rush hour* → `when is rush hour at the
station`, `what time is the station busiest`. The first four phrases the
chooser sees (round-robin) cover both outcomes. No reserved word (today,
tomorrow, calendar, schedule, search…) appears in any phrase, and the word
"by" is not in `app.summary`.

**Prompts to try once it is installed** (without the @ first, then with it):

- Should reach it: "how busy is Oxford Circus at 5:30pm on a Friday",
  "when is Bank quietest on a weekday morning", "is King's Cross busy at 8am",
  "best time to go through Waterloo on Saturday", "when's the rush hour at
  Canary Wharf"
- Worded differently: "is Victoria rammed after work", "will Stratford be
  packed at 6", "least busy time for Liverpool Street on Sunday"
- Should NOT reach it: "why is the Central line so hot", "is the Elizabeth
  line part of the tube", "change my commute to 9am"

<!-- TESTS -->

## Data

<!-- DATA -->

## Assumptions and decisions (made without asking, as instructed)

1. **Author name.** CLAUDE.md says to ask for a pen name and never invent one;
   you said never ask. You already publish another Pippa app from this Mac
   under the pen name `poppynoteaming`, so I reused it rather than asking or
   inventing one. Commits here use it and your GitHub no-reply address. To
   change it, edit `app_author_name` in `manifest.yaml` and run
   `git config user.name` with the same value.
2. **Failures raise a friendly error rather than returning a Result.**
   CLAUDE.md (which wins) says to fail by raising `AgentAppError` with a
   one-sentence `user_message`, and lists "returning empty data" as a mistake.
   So every failure (unknown station, typo with no close match, no TfL
   figures, unreadable day or time, missing data file) raises a subclass with
   one plain sentence, and any unexpected exception is turned into one too,
   so nothing ever crashes in front of a person.
3. **`station` is `required: false` in the manifest** although the app needs
   it. CLAUDE.md: a missing *required* input fails inside the runtime with a
   message blaming the app; optional lets the app say "Which tube station?
   Name one, for example Oxford Circus or King's Cross."
4. **"Powered by TfL Open Data" is not in `app.summary`.** CLAUDE.md forbids
   the word "by" in the summary (Pippa's badge parser splits on it) and the
   manifest has no app-level description field. The exact phrase is in the
   manifest's header comment, in the `source` output field's description and
   in `app.py`; the summary says "Uses TfL open data".
5. **`busyness_pct` is rescaled per station.** TfL's `percentageOfBaseLine`
   is not relative to each station's own peak (Bank tops out at 61, Euston at
   14), so `crowding.json` keeps TfL's integers and `app.py` divides by the
   station's busiest fifteen minutes in the whole typical week: 100 = that
   station's normal peak. Weekday / weekend answers average Mon-Fri / Sat-Sun.
6. **Levels:** quiet under 30%, moderate 30-59%, busy 60-84%, very busy 85%+.
7. **Day:** empty means the current day in Europe/London (from
   `call.context.now`). Accepts day names and abbreviations, `weekday(s)`,
   `weekend`, `today`/`tonight`/`tomorrow`, ISO dates, `27/11` and
   `27 November`.
8. **Time:** `5:30pm`, `17:30`, `1730`, `8am`, `noon`, `midnight`,
   `half five`, `quarter to 6`, `now`. A bare hour from 1 to 6 with no am/pm is
   read as pm ("at 5" in a travel question means 17:00). For `crowding.at`
   with no time on the current day, the time is *now*; for another day with no
   time, `time`, `busyness_pct` and `level` are left out (declared optional)
   rather than invented. `crowding.quietest` never assumes now.
9. **Windows:** early morning 05-07, morning 07-12, lunchtime 11:30-14:30,
   afternoon 12-17, evening 17-21, night 21-24, off-peak 09:30-16:00,
   `rush hour` = from TfL's am peak start to its pm peak end ("quietest
   between the peaks"), `morning/evening rush` = TfL's own band, ranges like
   `4-7pm`, `after 6pm`, `before 8am`. With no window but a time: two hours
   either side of it. With neither: 07:00-22:00.
10. **Stations kept:** all 272 `NaptanMetroStation` entries, the 270 `940GZZLU…`
    ones plus Battersea Power Station and Nine Elms (`940GZZBPSUST`,
    `940GZZNEUGST`), which are tube stations too.
11. **Same-named stations** (TfL lists two Hammersmiths and two Edgware Roads)
    are told apart by their lines in the reply; a bare name picks the busier.
12. **Fuzzy matching** (difflib): apostrophes (straight or curly), `&`/`and`,
    `St`/`Street`/`Saint`, `Rd`, `Ct`, `Sq`, `X` for Cross, `T5` for
    Terminal 5, plus a small alias table (KX, TCR, Heathrow, Elephant,
    Highbury, Olympia, Wembley…). A near miss is refused with "did you mean
    …?" instead of being guessed.
13. **Tests and checks ran locally against a stand-in for the Pippa SDK.**
    CLAUDE.md says not to install the SDK (only the build has it), so I wrote
    a strict local stand-in in the session scratchpad (not in the repository)
    to run `test_app.py`, `scripts/check.py` and `scripts/routing_check.py`
    here. The build's own run with the real SDK is the one that counts.
14. `scripts/build_data.py` is a new file in `scripts/` (not deployed, as it
    should not be). No existing file in `scripts/`, `.githooks`, `.claude`,
    `.github`, `conftest.py` or `pytest.ini` was changed.
15. **GitHub invitation.** The first push was refused (no write access yet):
    your GitHub account had a pending write invitation for this repository
    from Pippa Labs. The README says to accept it, and pushing was part of the
    brief, so I accepted that one invitation (only this repository's) and
    pushed.
16. **First push carried partial data.** I pushed once the tests were green
    with Zone 1 and most of Zone 2 downloaded (all five demo stations
    included), to prove the build early, then pushed again with the full
    file. Both are the same version, 0.1.0, which is still a draft, so the
    second push replaced the first.

## Risks

- **Typical week, not live.** TfL's crowding profiles describe a normal week.
  Strikes, closures, events, bank holidays, Christmas and weather are not in
  them, and the app says so only through its `source` label. "Live" crowding
  is deliberately never called (TfL blocks Pippa's servers anyway).
- **Data age.** "Downloaded 2026-11-20" is when it was fetched, not when TfL
  measured it. Re-running `python3 scripts/build_data.py --fresh` takes about
  40 minutes because TfL limits anonymous use to roughly 50 requests a minute
  (it answers 429 with a 56-second Retry-After; the script waits and resumes).
- **Rescaling** makes a quiet station's profile look as "busy" as a packed
  one at its own peak: 90% at a small station is far fewer people than 90%
  at Bank. That is what "compared with normal" means, and the reply says
  "of its peak", but it is worth knowing.
- **Routing.** Pippa may answer "is the tube busy" herself, or send it to
  another transport app if one is installed; the phrases are station-centred
  to help. Nothing local can prove routing: try the prompts below once live.
- **Ambiguous names.** Very short names ("Ken") match the shortest station
  starting with them; "Heathrow" means Terminals 2 & 3; "Bank and Monument"
  means Bank. Stations TfL has no figures for get "TfL has no crowding
  figures for X." (list above).
- **Bare hours 1-6 read as pm**, which is wrong for someone asking about a
  6am start (they can say "6am").
- **Pen name.** This app reuses `poppynoteaming`. If you would rather have a
  different pen name for it, change `app_author_name` and
  `git config user.name` together, then push.
- **Assumed the stand-in SDK matches the real one** closely enough; if the
  build's tests disagree, the build is right (see the build result above).
