"""Download TfL's tube crowding figures into crowding.json, beside app.py.

Run it on a computer that can reach api.tfl.gov.uk (Pippa's servers get a
403 from TfL, so the app never calls TfL itself; it reads this file):

    python3 scripts/build_data.py

What it does:

1. Downloads the tube station list (https://api.tfl.gov.uk/StopPoint/Mode/tube,
   about 16 MB) and keeps the NaptanMetroStation entries (about 270).
2. For every station and every day (Mon..Sun) downloads
   https://api.tfl.gov.uk/crowding/<naptanId>/<Day>: the AM and PM peak bands
   and 96 fifteen-minute bands, each a fraction of the station's baseline.
3. Writes crowding.json (compact JSON, well under 1.5 MB):

       {"source": "TfL open data", "downloaded": "YYYY-MM-DD",
        "stations": {"940GZZLUOXC": {"name": "Oxford Circus", "lat": .., "lon": ..,
                     "zone": "1", "lines": ["Bakerloo", ..],
                     "days": {"Mon": [96 ints 0-100+], ..},
                     "am": {"Mon": "07:45-09:45", ..}, "pm": {"Mon": "17:00-19:00", ..}}},
        "no_data": {"940GZZ...": "Station name"},
        "always_quiet": ["940GZZ..."]}     # no_data stations TfL marks isAlwaysQuiet

Zone 1 stations are fetched first, 8 at a time, and the file is saved after
every station, so a run that is stopped part way still leaves a useful file,
and running it again carries on where it stopped (pass --fresh to start over).
A 429 from TfL is backed off and retried. It never calls the live crowding
endpoint. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "crowding.json"
STATIONS_URL = "https://api.tfl.gov.uk/StopPoint/Mode/tube"
CROWDING_URL = "https://api.tfl.gov.uk/crowding/{naptan}/{day}"
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
USER_AGENT = "quiet-time-data-build/0.1 (Pippa agent app; TfL open data)"
WORKERS = 8
MAX_TRIES = 6

_save_lock = threading.Lock()


def fetch(url: str, timeout: float = 30.0) -> bytes | None:
    """GET a URL. None for a 404 (no data); backs off on 429 and 5xx."""
    delay = 2.0
    for attempt in range(1, MAX_TRIES + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code == 429 or exc.code >= 500:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = float(retry_after) if retry_after else delay
                except ValueError:
                    wait = delay
                print(f"  {exc.code} on {url}; waiting {wait:.0f}s (try {attempt})", flush=True)
                time.sleep(min(wait, 60))
                delay = min(delay * 2, 60)
                continue
            raise
        except (TimeoutError, urllib.error.URLError) as exc:
            print(f"  network error on {url}: {exc}; retrying in {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 60)
    raise RuntimeError(f"gave up on {url} after {MAX_TRIES} tries")


def clean_name(common_name: str) -> str:
    """TfL's name without " Underground Station". Line suffixes such as
    "(Dist&Picc Line)" are removed too; app.py tells same-named stations apart
    by their lines."""
    name = common_name
    for suffix in (" Underground Station", "-Underground", " (Dist&Picc Line)", " (H&C Line)"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def zone_of(stop: dict) -> str:
    for prop in stop.get("additionalProperties") or []:
        if prop.get("key") == "Zone":
            return str(prop.get("value") or "")
    return ""


def zone_rank(zone: str) -> int:
    digits = [int(part) for part in zone.replace("/", "+").split("+") if part.strip().isdigit()]
    return min(digits) if digits else 99


def load_stations(stations_file: str | None) -> list[dict]:
    if stations_file:
        raw = Path(stations_file).read_bytes()
    else:
        print(f"Downloading station list from {STATIONS_URL} ...", flush=True)
        raw = fetch(STATIONS_URL, timeout=120)
        if raw is None:
            sys.exit("TfL returned no station list")
    payload = json.loads(raw)
    stops = payload["stopPoints"] if isinstance(payload, dict) else payload
    stations = []
    seen = set()
    for stop in stops:
        if stop.get("stopType") != "NaptanMetroStation":
            continue
        naptan = stop.get("naptanId") or stop.get("id")
        if not naptan or naptan in seen:
            continue
        seen.add(naptan)
        stations.append({
            "naptan": naptan,
            "name": clean_name(stop.get("commonName", naptan)),
            "lat": round(float(stop.get("lat") or 0), 5),
            "lon": round(float(stop.get("lon") or 0), 5),
            "zone": zone_of(stop),
            "lines": sorted({line.get("name", "") for line in stop.get("lines") or [] if line.get("name")}),
        })
    stations.sort(key=lambda s: (zone_rank(s["zone"]), s["name"]))
    return stations


def parse_day(raw: bytes | None) -> tuple[list[int], str, str] | None:
    """96 ints (percent of baseline) and the AM/PM peak bands, or None."""
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    bands = body.get("timeBands") or []
    if len(bands) != 96:
        return None
    values = []
    for band in bands:
        value = band.get("percentageOfBaseLine")
        if value is None:
            return None
        values.append(int(round(float(value) * 100)))
    if not any(values):
        return None
    return values, body.get("amPeakTimeBand") or "", body.get("pmPeakTimeBand") or ""


def always_quiet(raw: bytes | None) -> bool:
    """TfL's isAlwaysQuiet flag, sent with an empty answer for some stations."""
    try:
        return bool(raw) and json.loads(raw).get("isAlwaysQuiet") is True
    except (ValueError, AttributeError):
        return False


def fetch_station(station: dict) -> dict:
    days, am, pm = {}, {}, {}
    quiet = False
    for day in DAYS:
        url = CROWDING_URL.format(naptan=quote(station["naptan"], safe=""), day=day)
        raw = fetch(url)
        parsed = parse_day(raw)
        if parsed is None:
            quiet = quiet or always_quiet(raw)
            continue
        values, am_band, pm_band = parsed
        days[day] = values
        am[day] = am_band
        pm[day] = pm_band
    return {"days": days, "am": am, "pm": pm, "always_quiet": quiet}


def recheck_no_data(out: Path, data: dict) -> None:
    """Ask TfL once more (Monday) about each station without figures, and
    record the ones it marks isAlwaysQuiet."""
    flagged = set(data.get("always_quiet", []))
    for naptan, name in sorted(data["no_data"].items(), key=lambda item: item[1]):
        url = CROWDING_URL.format(naptan=quote(naptan, safe=""), day="Mon")
        raw = fetch(url)
        if parse_day(raw) is None and always_quiet(raw):
            flagged.add(naptan)
        print(f"  {name}: {'always quiet' if naptan in flagged else 'no figures'}", flush=True)
    data["always_quiet"] = sorted(flagged)
    save(out, data)


def save(out: Path, data: dict) -> None:
    tmp = out.with_name(out.name + ".part")
    tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--stations-file", help="use a saved copy of the station list")
    parser.add_argument("--fresh", action="store_true", help="ignore an existing file")
    parser.add_argument("--limit", type=int, default=0, help="only the first N stations")
    parser.add_argument("--recheck-no-data", action="store_true",
                        help="only re-ask TfL about the stations without figures")
    args = parser.parse_args()
    out = Path(args.out)

    data = {"source": "TfL open data", "downloaded": date.today().isoformat(),
            "stations": {}, "no_data": {}, "always_quiet": []}
    if out.exists() and not args.fresh:
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
            data["stations"] = previous.get("stations", {})
            data["no_data"] = previous.get("no_data", {})
            data["always_quiet"] = previous.get("always_quiet", [])
            data["downloaded"] = previous.get("downloaded", data["downloaded"])
            print(f"Resuming: {len(data['stations'])} stations already in {out.name}", flush=True)
        except ValueError:
            pass

    if args.recheck_no_data:
        recheck_no_data(out, data)
        return

    stations = load_stations(args.stations_file)
    if args.limit:
        stations = stations[: args.limit]
    todo = [s for s in stations if s["naptan"] not in data["stations"] and s["naptan"] not in data["no_data"]]
    print(f"{len(stations)} stations; {len(todo)} to fetch, {WORKERS} at a time", flush=True)

    done = 0
    failed = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_station, s): s for s in todo}
        for future in as_completed(futures):
            station = futures[future]
            done += 1
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                failed.append(station["name"])
                print(f"[{done}/{len(todo)}] FAILED {station['name']}: {exc}", flush=True)
                continue
            with _save_lock:
                quiet = result.pop("always_quiet")
                if result["days"]:
                    data["stations"][station["naptan"]] = {
                        "name": station["name"], "lat": station["lat"], "lon": station["lon"],
                        "zone": station["zone"], "lines": station["lines"], **result,
                    }
                else:
                    data["no_data"][station["naptan"]] = station["name"]
                    if quiet:
                        data.setdefault("always_quiet", [])
                        if station["naptan"] not in data["always_quiet"]:
                            data["always_quiet"].append(station["naptan"])
                data["downloaded"] = date.today().isoformat()
                save(out, data)
            status = f"{len(result['days'])} days" if result["days"] else "no data"
            print(f"[{done}/{len(todo)}] {station['name']} (zone {station['zone']}): {status}", flush=True)

    size = out.stat().st_size if out.exists() else 0
    print(f"Done: {len(data['stations'])} stations with figures, {len(data['no_data'])} without, "
          f"{len(failed)} failed; {out.name} is {size / 1024:.0f} KB", flush=True)
    if failed:
        print("Failed (run again to retry): " + ", ".join(sorted(failed)), flush=True)


if __name__ == "__main__":
    main()
