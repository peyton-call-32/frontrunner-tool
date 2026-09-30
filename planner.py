"""Plan any trip between FrontRunner stations and UVX stops.

A journey is either one ride (train only, or UVX only) or two rides with a transfer at a
station both serve (Provo Central, Orem Central). Saved commutes still use commute.py.
"""
import re

from commute import mins, rides
from frontrunner import seconds

TRAIN, UVX = "FrontRunner", "UVX"


def base(name):
    """'Provo Central Station (Bay H)' -> 'Provo Central Station'."""
    return re.sub(r" \(.*\)$", "", name)


def served(trips):
    """Rider stops served by these trips (skips crew-only points like 'WARM SPRINGS RELIEF POINT')."""
    return {base(s[0]) for stops in trips for s in stops if base(s[0]).endswith("Station")}


def places(fr, uvx):
    """(FrontRunner stations, UVX-only stops). Stations served by both are listed with FrontRunner."""
    stations = served(fr)
    return sorted(stations), sorted(served(uvx) - stations)


def journeys(fr, uvx, origin, destination, walk=3):
    """Every sensible way from origin to destination, sorted by departure.

    Each journey is a list of legs: dict(mode, board, board_at, alight, alight_at, headsign).
    Journeys that leave earlier but don't arrive earlier than another are dropped.
    """
    by_mode = {TRAIN: fr, UVX: uvx}
    names = {TRAIN: served(fr), UVX: served(uvx)}
    found = []

    def legs(mode, a, b):
        if a not in names[mode] or b not in names[mode]:
            return []
        return [dict(mode=mode, board=dep, board_at=a, alight=arr, alight_at=b, headsign=hs)
                for dep, arr, hs in rides(by_mode[mode], a, b)]

    # One ride
    for mode in (TRAIN, UVX):
        found += [[leg] for leg in legs(mode, origin, destination)]

    # Two rides, changing at a station both serve
    for hub in sorted(names[TRAIN] & names[UVX] - {origin, destination}):
        for first, second in ((TRAIN, UVX), (UVX, TRAIN)):
            onward = legs(second, hub, destination)
            if not onward:
                continue
            for leg1 in legs(first, origin, hub):
                leg2 = next((l for l in onward if mins(leg1["alight"], l["board"]) >= walk), None)
                if leg2:
                    found.append([leg1, leg2])

    return best_only(found)


def best_only(found):
    """Drop a journey if another leaves at the same time or later and arrives at the same time or earlier."""
    dep = lambda j: seconds(j[0]["board"])
    arr = lambda j: seconds(j[-1]["alight"])
    kept, best_arrival = [], None
    for j in sorted(found, key=lambda j: (-dep(j), arr(j), len(j))):
        if best_arrival is None or arr(j) < best_arrival:
            kept.append(j)
            best_arrival = arr(j)
    return sorted(kept, key=dep)

