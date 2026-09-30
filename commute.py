"""Holladay ⇄ BYU commute: FrontRunner (Murray Central ⇄ Provo Central) + UVX (Provo Central ⇄ BYU South Campus).

Usage:
  python3 commute.py                  # morning and evening tables for today
  python3 commute.py --arrive-by 9:00 # which Murray train gets me to BYU by 9:00 AM
"""
import sys
from datetime import date

from frontrunner import active_services, fmt, load_feed, rows, seconds

HOME = "Murray Central Station"
TRANSFER = "Provo Central Station"
CAMPUS = "BYU South Campus Station"
WALK = 3  # minutes to walk between the train platform and the UVX bay at Provo


def load_trips(feed, services, route_names):
    """{route_name: [[(stop_name, arrival, departure, headsign), ...] per trip]} for today's trips."""
    routes = {
        r["route_id"]: name for r in rows(feed, "routes.txt")
        for name in route_names if name in (r["route_short_name"], r["route_long_name"])
    }
    trips = {
        r["trip_id"]: (routes[r["route_id"]], r["trip_headsign"]) for r in rows(feed, "trips.txt")
        if r["route_id"] in routes and r["service_id"] in services
    }
    names = {r["stop_id"]: r["stop_name"] for r in rows(feed, "stops.txt")}

    stop_times = {}
    for r in rows(feed, "stop_times.txt"):
        if r["trip_id"] in trips:
            stop_times.setdefault(r["trip_id"], []).append(r)

    out = {name: [] for name in route_names}
    for tid, sts in stop_times.items():
        route, headsign = trips[tid]
        sts.sort(key=lambda r: int(r["stop_sequence"]))
        out[route].append([
            (names[r["stop_id"]], r["arrival_time"].strip(), r["departure_time"].strip(), headsign) for r in sts
        ])
    return out


def rides(trips, origin, destination):
    """(leave origin, arrive destination, headsign) for every trip that goes origin → destination."""
    result = []
    for stops in trips:
        start = next((i for i, s in enumerate(stops) if s[0].startswith(origin)), None)
        if start is None:
            continue
        end = next((s for s in stops[start + 1:] if s[0].startswith(destination)), None)
        if end:
            result.append((stops[start][2], end[1], stops[start][3]))
    return sorted(result, key=lambda r: seconds(r[0]))


def mins(a, b):
    return (seconds(b) - seconds(a)) // 60


def morning_plans(fr, uvx):
    """For each southbound train from Murray: (train, first UVX that works or None)."""
    buses = rides(uvx, TRANSFER, CAMPUS)
    plans = []
    for train in rides(fr, HOME, TRANSFER):
        bus = next((b for b in buses if mins(train[1], b[0]) >= WALK), None)
        plans.append((train, bus))
    return plans


def evening_plans(fr, uvx):
    """For each northbound train from Provo to Murray: (latest UVX that works or None, train)."""
    buses = sorted(rides(uvx, CAMPUS, TRANSFER), key=lambda b: seconds(b[1]))
    plans = []
    for train in rides(fr, TRANSFER, HOME):
        ok = [b for b in buses if mins(b[1], train[0]) >= WALK]
        plans.append((ok[-1] if ok else None, train))
    return plans


def print_morning(plans):
    print(f"MORNING — Murray Central → Provo Central → BYU South Campus ({WALK} min walk at Provo)\n")
    print(f"  {'Leave Murray':>12}  {'Arrive Provo':>12}  {'UVX leaves':>10}  {'Arrive BYU':>10}  Total")
    for train, bus in plans:
        if bus:
            total = mins(train[0], bus[1])
            print(f"  {fmt(train[0]):>12}  {fmt(train[1]):>12}  {fmt(bus[0]):>10}  {fmt(bus[1]):>10}  {total} min")
        else:
            print(f"  {fmt(train[0]):>12}  {fmt(train[1]):>12}  {'no UVX after this train':>24}")


def print_evening(plans):
    print(f"EVENING — BYU South Campus → Provo Central → Murray Central ({WALK} min walk at Provo)\n")
    print(f"  {'Leave BYU':>10}  {'UVX arrives':>11}  {'Spare':>6}  {'Train leaves':>12}  {'Arrive Murray':>13}  Train to")
    for bus, train in plans:
        if bus:
            spare = mins(bus[1], train[0])
            print(f"  {fmt(bus[0]):>10}  {fmt(bus[1]):>11}  {spare:>2} min  {fmt(train[0]):>12}  {fmt(train[1]):>13}  {train[2]}")
        else:
            print(f"  {'no UVX in time':>25}  {'':>6}  {fmt(train[0]):>12}  {fmt(train[1]):>13}  {train[2]}")


def arrive_by(plans, deadline):
    """Print the latest Murray train that gets to BYU South Campus by deadline (e.g. '9:00' or '14:30')."""
    h, m = map(int, deadline.split(":"))
    target = f"{h}:{m:02d}:00"
    ok = [(t, b) for t, b in plans if b and seconds(b[1]) <= seconds(target)]
    if not ok:
        print(f"No train from Murray gets you to BYU South Campus by {fmt(target)} today.")
        return
    train, bus = max(ok, key=lambda p: seconds(p[0][0]))
    print(f"To be at BYU South Campus by {fmt(target)}:")
    print(f"  Catch the {fmt(train[0])} train at Murray Central")
    print(f"  Arrive Provo Central {fmt(train[1])}, take the {fmt(bus[0])} UVX")
    print(f"  Arrive BYU South Campus {fmt(bus[1])} ({mins(bus[1], target)} min early)")


def main():
    feed = load_feed()
    today = date.today()
    trips = load_trips(feed, active_services(feed, today), ["FrontRunner", "830X"])
    fr, uvx = trips["FrontRunner"], trips["830X"]

    print(f"{today:%A, %b %d, %Y}\n")
    if "--arrive-by" in sys.argv:
        arrive_by(morning_plans(fr, uvx), sys.argv[sys.argv.index("--arrive-by") + 1])
        return
    print_morning(morning_plans(fr, uvx))
    print()
    print_evening(evening_plans(fr, uvx))


if __name__ == "__main__":
    main()
