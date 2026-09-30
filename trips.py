"""My trips: save a commute by name and run it later.

Each student's trips are stored separately (one JSON file per user in trips/).
TripStore is the only code that touches storage, so a website can swap it for a database.

Usage:
  python3 trips.py list
  python3 trips.py run "Morning to BYU" [--arrive-by 9:00]
  python3 trips.py add "Morning to BYU" --direction to-campus --station "Murray Central" --stop "BYU South Campus" [--walk 3] [--arrive-by 9:00]
  python3 trips.py add "To SLC" --direction any --from "Murray Central" --to "Salt Lake Central"
  python3 trips.py edit "Morning to BYU" [--new-name ...] [--direction ...] [--station ...] [--stop ...] [--walk ...] [--arrive-by ... | --arrive-by none]
  python3 trips.py delete "Morning to BYU"
Add --user NAME to any command to use another student's trips (default: me).
"""
import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from commute import (
    arrive_by, evening_plans, load_trips, morning_plans, print_evening, print_morning, short,
)
from frontrunner import active_services, fmt, load_feed, seconds
from planner import journeys, places

TRIPS_DIR = Path(__file__).parent / "trips"
# to-campus: train then UVX via Provo Central; home: UVX then train via Provo Central;
# any: planned from start to end (train, UVX, or both), see planner.py
DIRECTIONS = ("to-campus", "home", "any")


@dataclass
class Trip:
    name: str
    direction: str       # "to-campus", "home" or "any"
    station: str         # to-campus/home: FrontRunner station, e.g. "Murray Central Station"
    stop: str            # to-campus/home: UVX stop, e.g. "BYU South Campus Station"
    walk: int = 3        # minutes to transfer between train and UVX
    arrive_by: Optional[str] = None  # not for "home" trips, e.g. "9:00"
    start: Optional[str] = None      # "any" trips: where you start, e.g. "Murray Central Station"
    end: Optional[str] = None        # "any" trips: where you're going


class TripStore:
    """Saved trips for one user, kept in trips/<user>.json."""

    def __init__(self, user):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", user):
            raise ValueError("User name can only use letters, numbers, - and _")
        self.path = TRIPS_DIR / f"{user}.json"

    def all(self):
        if not self.path.exists():
            return []
        return [Trip(**t) for t in json.loads(self.path.read_text())]

    def get(self, name):
        return next((t for t in self.all() if t.name.lower() == name.lower()), None)

    def save(self, trip, replacing=None):
        """Add trip, or replace the trip named `replacing` (lets a trip be renamed)."""
        trips = self.all()
        if replacing:
            trips = [t for t in trips if t.name.lower() != replacing.lower()]
        if any(t.name.lower() == trip.name.lower() for t in trips):
            raise ValueError(f"You already have a trip called {trip.name!r}")
        trips.append(trip)
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps([asdict(t) for t in trips], indent=2) + "\n")

    def delete(self, name):
        trips = self.all()
        keep = [t for t in trips if t.name.lower() != name.lower()]
        if len(keep) == len(trips):
            return False
        self.path.write_text(json.dumps([asdict(t) for t in keep], indent=2) + "\n")
        return True


def validate(trip, fr, uvx):
    """Check the trip against today's feed and fill in full stop names. Raises ValueError."""
    if not trip.name.strip() or "/" in trip.name:
        raise ValueError("Trip name can't be empty or contain /")
    if trip.direction not in DIRECTIONS:
        raise ValueError(f"Direction must be one of: {', '.join(DIRECTIONS)}")
    if trip.walk < 0:
        raise ValueError("Walk time can't be negative")
    if trip.arrive_by:
        if trip.direction == "home":
            raise ValueError("Arrive-by doesn't apply to going-home trips")
        if not re.fullmatch(r"\d{1,2}:\d{2}", trip.arrive_by):
            raise ValueError("Arrive-by time should look like 9:00 or 14:30")
    if trip.direction == "any":
        stations, stops = places(fr, uvx)
        trip.start = resolve_name(trip.start or "", stations + stops, "station or stop")
        trip.end = resolve_name(trip.end or "", stations + stops, "station or stop")
        if trip.start == trip.end:
            raise ValueError("Start and destination are the same")
        trip.station = trip.stop = ""
        return
    trip.start = trip.end = None
    trip.station = resolve(trip.station, fr, "FrontRunner station")
    trip.stop = resolve(trip.stop, uvx, "UVX stop")


def resolve(text, trips, kind):
    """Match what the user typed to a real stop name served by these trips."""
    names = {s[0] for stops in trips for s in stops}
    # Station platforms/bays share a base name, e.g. "Provo Central Station (Bay H)"
    return resolve_name(text, {re.sub(r" \(.*\)$", "", n) for n in names}, kind)


def resolve_name(text, names, kind):
    """Match what the user typed to one of names."""
    if not text.strip():
        raise ValueError(f"Choose a {kind}")
    want = text.lower().removesuffix(" station")
    matches = sorted(n for n in names if n.lower().removesuffix(" station") == want) or \
        sorted(n for n in names if want in n.lower())
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"No {kind} matches {text!r}")
    raise ValueError(f"{text!r} matches several {kind}s: {', '.join(matches)}")


def load_day(day):
    feed = load_feed()
    t = load_trips(feed, active_services(feed, day), ["FrontRunner", "830X"])
    return t["FrontRunner"], t["830X"]


def load_today():
    today = date.today()
    return (today, *load_day(today))


def describe(t):
    if t.direction == "any":
        route = f"{short(t.start)} → {short(t.end)}"
    elif t.direction == "to-campus":
        route = f"{short(t.station)} → train → UVX → {short(t.stop)}"
    else:
        route = f"{short(t.stop)} → UVX → train → {short(t.station)}"
    extra = f", arrive by {t.arrive_by}" if t.arrive_by else ""
    return f"{t.name}: {route} ({t.walk} min walk{extra})"


def run(trip, arrive, today, fr, uvx):
    print(f"{trip.name} — {today:%A, %b %d, %Y}\n")
    if trip.direction == "any":
        options = journeys(fr, uvx, trip.start, trip.end, trip.walk)
        if arrive:
            h, m = map(int, arrive.split(":"))
            options = [j for j in options if seconds(j[-1]["alight"]) <= seconds(f"{h}:{m:02d}:00")][-1:]
        for j in options:
            print("  " + "  then  ".join(
                f'{l["mode"]} {fmt(l["board"])} {short(l["board_at"])} → {fmt(l["alight"])} {short(l["alight_at"])}'
                for l in j))
        if not options:
            print("No options.")
    elif trip.direction == "to-campus":
        plans = morning_plans(fr, uvx, trip.station, trip.stop, trip.walk)
        if arrive:
            arrive_by(plans, arrive, trip.station, trip.stop)
        else:
            print_morning(plans, trip.station, trip.stop, trip.walk)
    else:
        print_evening(evening_plans(fr, uvx, trip.station, trip.stop, trip.walk), trip.station, trip.stop, trip.walk)


def main():
    p = argparse.ArgumentParser(description="Save and run your usual commutes.")
    p.add_argument("--user", default="me", help="whose trips to use (default: me)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--arrive-by", help="override the saved arrive-by time")
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--direction", required=True, choices=DIRECTIONS)
    a.add_argument("--station", default="", help="to-campus/home trips")
    a.add_argument("--stop", default="", help="to-campus/home trips")
    a.add_argument("--from", dest="start", help="any trips")
    a.add_argument("--to", dest="end", help="any trips")
    a.add_argument("--walk", type=int, default=3)
    a.add_argument("--arrive-by")
    e = sub.add_parser("edit")
    e.add_argument("name")
    e.add_argument("--new-name")
    e.add_argument("--direction", choices=DIRECTIONS)
    e.add_argument("--station")
    e.add_argument("--stop")
    e.add_argument("--from", dest="start")
    e.add_argument("--to", dest="end")
    e.add_argument("--walk", type=int)
    e.add_argument("--arrive-by", help='time like 9:00, or "none" to clear')
    d = sub.add_parser("delete")
    d.add_argument("name")
    args = p.parse_args()

    try:
        store = TripStore(args.user)
        if args.cmd == "list":
            trips = store.all()
            if not trips:
                print("No saved trips yet. Add one with: python3 trips.py add ...")
            for t in trips:
                print(describe(t))

        elif args.cmd == "run":
            trip = store.get(args.name)
            if not trip:
                raise ValueError(f"No trip called {args.name!r}. See: python3 trips.py list")
            run(trip, args.arrive_by or trip.arrive_by, *load_today())

        elif args.cmd == "add":
            if store.get(args.name):
                raise ValueError(f"You already have a trip called {args.name!r}. Use edit to change it.")
            trip = Trip(args.name, args.direction, args.station, args.stop, args.walk, args.arrive_by,
                        args.start, args.end)
            _, fr, uvx = load_today()
            validate(trip, fr, uvx)
            store.save(trip)
            print(f"Saved. {describe(trip)}")

        elif args.cmd == "edit":
            trip = store.get(args.name)
            if not trip:
                raise ValueError(f"No trip called {args.name!r}")
            old_name = trip.name
            for field in ("direction", "station", "stop", "walk", "start", "end"):
                if getattr(args, field) is not None:
                    setattr(trip, field, getattr(args, field))
            if args.new_name:
                trip.name = args.new_name
            if args.arrive_by is not None:
                trip.arrive_by = None if args.arrive_by.lower() == "none" else args.arrive_by
            _, fr, uvx = load_today()
            validate(trip, fr, uvx)
            store.save(trip, replacing=old_name)
            print(f"Updated. {describe(trip)}")

        elif args.cmd == "delete":
            if not store.delete(args.name):
                raise ValueError(f"No trip called {args.name!r}")
            print(f"Deleted {args.name!r}.")
    except ValueError as err:
        sys.exit(f"Error: {err}")


if __name__ == "__main__":
    main()
