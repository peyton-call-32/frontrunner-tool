"""Match each UVX bus arriving at Provo Central to the next FrontRunner train."""
import sys
from datetime import date

from frontrunner import active_services, fmt, load_feed, rows, seconds, train_departures

ROUTE = "830X"
MIN_TRANSFER = 3  # minutes to walk from the bus bay to the platform
MY_STOP = "BYU South Campus Station"  # used by --by-train


def uvx_arrivals(feed, services):
    """Times UVX buses finish their trip at Provo Central, sorted."""
    route_ids = {r["route_id"] for r in rows(feed, "routes.txt") if r["route_short_name"] == ROUTE}
    trips = {
        r["trip_id"] for r in rows(feed, "trips.txt")
        if r["route_id"] in route_ids and r["service_id"] in services
    }
    provo = {r["stop_id"] for r in rows(feed, "stops.txt") if r["stop_name"].startswith("Provo Central Station")}

    last = {}  # trip_id -> (stop_sequence, stop_id, arrival_time)
    for r in rows(feed, "stop_times.txt"):
        if r["trip_id"] in trips:
            seq = int(r["stop_sequence"])
            if seq > last.get(r["trip_id"], (0,))[0]:
                last[r["trip_id"]] = (seq, r["stop_id"], r["arrival_time"].strip())

    return sorted((t for _, stop, t in last.values() if stop in provo), key=seconds)


def uvx_trips_from(feed, services, stop_name):
    """(leaves stop_name, arrives Provo Central) for UVX trips that ride from that stop to Provo."""
    route_ids = {r["route_id"] for r in rows(feed, "routes.txt") if r["route_short_name"] == ROUTE}
    trips = {
        r["trip_id"] for r in rows(feed, "trips.txt")
        if r["route_id"] in route_ids and r["service_id"] in services
    }
    names = {r["stop_id"]: r["stop_name"] for r in rows(feed, "stops.txt")}

    by_trip = {}
    for r in rows(feed, "stop_times.txt"):
        if r["trip_id"] in trips:
            by_trip.setdefault(r["trip_id"], []).append(r)

    rides = []
    for stop_times in by_trip.values():
        stop_times.sort(key=lambda r: int(r["stop_sequence"]))
        end = stop_times[-1]
        if not names[end["stop_id"]].startswith("Provo Central Station"):
            continue
        board = next((r for r in stop_times if names[r["stop_id"]] == stop_name), None)
        if board:
            rides.append((board["departure_time"].strip(), end["arrival_time"].strip()))
    return sorted(rides, key=lambda r: seconds(r[1]))


def by_train(feed, services, today):
    trains = train_departures(feed, services)
    rides = uvx_trips_from(feed, services, MY_STOP)
    if not rides:
        sys.exit(f"No UVX trips from {MY_STOP!r} to Provo Central today. Check the stop name.")

    print(f"Latest UVX from {MY_STOP} for each FrontRunner at Provo — {today:%A, %b %d, %Y}")
    print(f"(needs at least {MIN_TRANSFER} min to transfer)\n")
    print(f"  {'Train leaves':>12}   {'Leave stop':>10}   {'Bus arrives':>11}   Wait    To")
    for train_time, headsign in trains:
        ok = [r for r in rides if seconds(train_time) - seconds(r[1]) >= MIN_TRANSFER * 60]
        if ok:
            leave, arrive = max(ok, key=lambda r: seconds(r[1]))
            wait = (seconds(train_time) - seconds(arrive)) // 60
            print(f"  {fmt(train_time):>12}   {fmt(leave):>10}   {fmt(arrive):>11}   {wait:>2} min  {headsign}")
        else:
            print(f"  {fmt(train_time):>12}   {'no bus in time':>10}{'':>17}{headsign}")


def main():
    feed = load_feed()
    today = date.today()
    services = active_services(feed, today)
    if "--by-train" in sys.argv:
        return by_train(feed, services, today)
    trains = train_departures(feed, services)
    buses = uvx_arrivals(feed, services)

    print(f"UVX → FrontRunner at Provo Central — {today:%A, %b %d, %Y}")
    print(f"(needs at least {MIN_TRANSFER} min to transfer)\n")
    print(f"  {'Bus arrives':>11}   {'Train leaves':>12}   Wait    To")
    for bus in buses:
        train = next((t for t in trains if seconds(t[0]) - seconds(bus) >= MIN_TRANSFER * 60), None)
        if train:
            wait = (seconds(train[0]) - seconds(bus)) // 60
            print(f"  {fmt(bus):>11}   {fmt(train[0]):>12}   {wait:>2} min  {train[1]}")
        else:
            print(f"  {fmt(bus):>11}   {'no more trains':>12}")
    print(f"\n{len(buses)} UVX arrivals")


if __name__ == "__main__":
    main()
