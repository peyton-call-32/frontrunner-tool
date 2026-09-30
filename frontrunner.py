"""Show today's FrontRunner departures from Provo Central, using UTA's GTFS feed."""
import csv
import io
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

GTFS_URL = "https://gtfsfeed.rideuta.com/GTFS.zip"
GTFS_ZIP = Path(__file__).parent / "data" / "GTFS.zip"
ROUTE_NAME = "FrontRunner"
STATION = "Provo Central Station"


def load_feed():
    if not GTFS_ZIP.exists() or "--refresh" in sys.argv:
        GTFS_ZIP.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(GTFS_URL, GTFS_ZIP)
    return zipfile.ZipFile(GTFS_ZIP)


def rows(feed, name):
    with feed.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))


def active_services(feed, day):
    ymd = day.strftime("%Y%m%d")
    weekday = day.strftime("%A").lower()
    services = {
        r["service_id"] for r in rows(feed, "calendar.txt")
        if r[weekday] == "1" and r["start_date"] <= ymd <= r["end_date"]
    }
    for r in rows(feed, "calendar_dates.txt"):
        if r["date"] == ymd:
            if r["exception_type"] == "1":
                services.add(r["service_id"])
            else:
                services.discard(r["service_id"])
    return services


def fmt(t):
    h, m, _ = map(int, t.strip().split(":"))
    suffix = "AM" if h % 24 < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {suffix}" + (" (+1)" if h >= 24 else "")


def seconds(t):
    h, m, s = map(int, t.strip().split(":"))
    return h * 3600 + m * 60 + s


def train_departures(feed, services):
    """Today's FrontRunner departures from Provo as (time, headsign), sorted."""
    route_ids = {r["route_id"] for r in rows(feed, "routes.txt") if r["route_long_name"] == ROUTE_NAME}
    trips = {
        r["trip_id"]: r["trip_headsign"] for r in rows(feed, "trips.txt")
        if r["route_id"] in route_ids and r["service_id"] in services
    }
    stop_ids = {r["stop_id"] for r in rows(feed, "stops.txt") if r["stop_name"] == STATION}

    # Last stop of each trip, so we can skip trips that end at Provo
    last_seq = {}
    departures = []
    for r in rows(feed, "stop_times.txt"):
        tid = r["trip_id"]
        if tid not in trips:
            continue
        seq = int(r["stop_sequence"])
        last_seq[tid] = max(last_seq.get(tid, 0), seq)
        if r["stop_id"] in stop_ids:
            departures.append((r["departure_time"].strip(), tid, seq))

    departures = [(t, trips[tid]) for t, tid, seq in departures if seq < last_seq[tid]]
    return sorted(departures, key=lambda d: seconds(d[0]))


def main():
    feed = load_feed()
    today = date.today()
    departures = train_departures(feed, active_services(feed, today))

    print(f"FrontRunner departures from {STATION} — {today:%A, %b %d, %Y}")
    if not departures:
        print("No trains today.")
    for t, headsign in departures:
        print(f"  {fmt(t):>9}  → {headsign}")
    print(f"{len(departures)} trains")


if __name__ == "__main__":
    main()
