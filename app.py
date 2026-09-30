"""FrontRunner Tool website: My trips on your phone.

Run:  .venv/bin/python app.py          → open http://localhost:8080 on this computer
      .venv/bin/python app.py --phone  → also reachable from your phone on the same Wi-Fi
"""
import re
import socket
import sys
from datetime import date, datetime, timedelta

from flask import Flask, abort, redirect, render_template, request, url_for

from commute import TRANSFER, best_arrive_by, evening_plans, mins, morning_plans, short
from frontrunner import fmt, seconds
from trips import DIRECTIONS, Trip, TripStore, load_day, validate

app = Flask(__name__)
USER = "me"  # one user for now; becomes the logged-in student once the site has accounts
TIGHT = 5    # transfers this many minutes or less get flagged
SOON = 10    # countdowns this many minutes or less get highlighted
RUSH = 5     # an option leaving in fewer minutes than this is "you may not make it"

_cache = {}


def feed_for(day):
    """FrontRunner and UVX trips for a day, kept for today and tomorrow (reading the schedule takes a few seconds)."""
    if day not in _cache:
        for old in [d for d in _cache if d < date.today()]:
            del _cache[old]
        fr, uvx = load_day(day)
        _cache[day] = dict(date=day, fr=fr, uvx=uvx, stations=stop_names(fr), stops=stop_names(uvx))
    return _cache[day]


def stop_names(trips):
    """Sorted stop names served by these trips, without bay/platform suffixes and without Provo Central."""
    names = {re.sub(r" \(.*\)$", "", s[0]) for stops in trips for s in stops}
    return sorted(n for n in names if not n.startswith(TRANSFER))


def at(day, gtfs_time):
    """Real clock time for a schedule time on a service day (schedule times can pass 24:00)."""
    return datetime.combine(day, datetime.min.time()) + timedelta(seconds=seconds(gtfs_time))


PROVO = short(TRANSFER)  # "Provo Central"


def to_campus_option(trip, day, train, bus):
    """Train from home station to Provo Central, then UVX to campus, written as directions."""
    home, campus = short(trip.station), short(trip.stop)
    return dict(
        leave=at(day, train[0]), time=fmt(train[0]), vehicle="train", start=home,
        dest=campus, arrive=fmt(bus[1]), total=mins(train[0], bus[1]), transfer=mins(train[1], bus[0]),
        steps=[
            dict(time=fmt(train[0]), text=f"Board FrontRunner at {home} (southbound)"),
            dict(time=fmt(train[1]), text=f"Get off at {PROVO}"),
            dict(walk="Walk to the UVX stop", wait="until the bus"),
            dict(time=fmt(bus[0]), text=f"Board UVX at {PROVO}"),
            dict(time=fmt(bus[1]), text=f"Get off at {campus}"),
        ],
    )


def home_option(trip, day, bus, train):
    """UVX from campus to Provo Central, then train to home station, written as directions."""
    home, campus = short(trip.station), short(trip.stop)
    return dict(
        leave=at(day, bus[0]), time=fmt(bus[0]), vehicle="UVX", start=campus,
        dest=home, arrive=fmt(train[1]), total=mins(bus[0], train[1]), transfer=mins(bus[1], train[0]),
        steps=[
            dict(time=fmt(bus[0]), text=f"Board UVX at {campus} (toward {PROVO})"),
            dict(time=fmt(bus[1]), text=f"Get off at {PROVO}"),
            dict(walk="Walk to the FrontRunner platform", wait="until the train"),
            dict(time=fmt(train[0]), text=f"Board FrontRunner at {PROVO} (northbound)"),
            dict(time=fmt(train[1]), text=f"Get off at {home}"),
        ],
    )


def options(trip, feed):
    """Every way to make the trip on feed's day, in the order you'd leave. Schedule math is in commute.py."""
    day = feed["date"]
    if trip.direction == "to-campus":
        plans = morning_plans(feed["fr"], feed["uvx"], trip.station, trip.stop, trip.walk)
        return [to_campus_option(trip, day, train, bus) for train, bus in plans if bus]
    plans = evening_plans(feed["fr"], feed["uvx"], trip.station, trip.stop, trip.walk)
    return [home_option(trip, day, bus, train) for bus, train in plans if bus]


def trip_view(trip, now):
    """What to show for a trip right now: the main answer, a backup if the main one is about to leave,
    the rest of today's options, and the ones already gone."""
    today = options(trip, feed_for(now.date()))
    ahead = [r for r in today if r["leave"] >= now]
    gone = [r for r in today if r["leave"] < now]
    _tomorrow = []

    def tomorrow_first():
        if not _tomorrow:
            _tomorrow.append(options(trip, feed_for(now.date() + timedelta(days=1))))
        return _tomorrow[0][0] if _tomorrow[0] else None

    v = dict(main=None, main_tomorrow=False, rush=False, backup=None, backup_tomorrow=False, later=[], gone=gone)
    if ahead:
        v["main"], later = ahead[0], ahead[1:]
    else:
        v["main"], v["main_tomorrow"], later = tomorrow_first(), True, []
    if v["main"] and not v["main_tomorrow"] and v["main"]["leave"] - now < timedelta(minutes=RUSH):
        v["rush"] = True
        if later:
            v["backup"], later = later[0], later[1:]
        else:
            v["backup"], v["backup_tomorrow"] = tomorrow_first(), True
    v["later"] = later
    return v


def countdown(leave, now):
    m = int((leave - now).total_seconds() // 60)
    if m < 1:
        return "leaving now"
    if m < 60:
        return f"leaves in {m} min"
    return f"leaves in {m // 60} h {m % 60} min"


def rush_text(leave, now):
    left = countdown(leave, now)
    return left[0].upper() + left[1:] + ", you may not make it"


def part_of_day(now):
    """Which kind of trip makes sense right now: to campus before noon, home after."""
    return "to-campus" if now.hour < 12 else "home"


@app.context_processor
def helpers():
    now = datetime.now()
    return dict(short=short, PROVO=PROVO, TIGHT=TIGHT, SOON=SOON, RUSH=RUSH,
                countdown=lambda leave: countdown(leave, now),
                rush_text=lambda leave: rush_text(leave, now),
                soon=lambda leave: (leave - now).total_seconds() <= SOON * 60,
                epoch_ms=lambda leave: int(leave.timestamp() * 1000),
                server_ms=lambda: int(now.timestamp() * 1000))


def get_trip(name):
    trip = TripStore(USER).get(name)
    if not trip:
        abort(404)
    return trip


@app.route("/")
def home():
    now = datetime.now()
    now_kind = part_of_day(now)
    trips = sorted(TripStore(USER).all(), key=lambda t: t.direction != now_kind)
    cards = [dict(trip=t, view=trip_view(t, now) if t.direction == now_kind else None) for t in trips]
    return render_template("index.html", cards=cards)


@app.route("/trip/<name>")
def show_trip(name):
    trip = get_trip(name)
    now = datetime.now()

    arrive = request.args.get("arrive_by") or trip.arrive_by
    plan = None
    if trip.direction == "to-campus" and arrive:
        if not re.fullmatch(r"\d{1,2}:\d{2}", arrive):
            abort(400)
        feed = feed_for(now.date())
        best = best_arrive_by(morning_plans(feed["fr"], feed["uvx"], trip.station, trip.stop, trip.walk), arrive)
        option = to_campus_option(trip, now.date(), *best) if best else None
        plan = dict(target=fmt(f"{arrive}:00"), option=option, gone=bool(option) and option["leave"] < now)

    return render_template("trip.html", trip=trip, v=trip_view(trip, now), arrive=arrive,
                           asked=bool(request.args.get("arrive_by")), plan=plan, today=now.date())


@app.route("/new", methods=["GET", "POST"])
def new_trip():
    return trip_form(None)


@app.route("/trip/<name>/edit", methods=["GET", "POST"])
def edit_trip(name):
    return trip_form(get_trip(name))


def trip_form(existing):
    feed = feed_for(date.today())
    error = None
    trip = existing or Trip("", "to-campus", "", "")
    if request.method == "POST":
        f = request.form
        try:
            walk = int(f.get("walk") or 3)
        except ValueError:
            walk = -1
        trip = Trip(f.get("name", "").strip(), f.get("direction", ""), f.get("station", ""),
                    f.get("stop", ""), walk, f.get("arrive_by") or None)
        try:
            if trip.direction != "to-campus":
                trip.arrive_by = None
            validate(trip, feed["fr"], feed["uvx"])
            TripStore(USER).save(trip, replacing=existing.name if existing else None)
            return redirect(url_for("show_trip", name=trip.name))
        except ValueError as err:
            error = str(err)
    return render_template("form.html", trip=trip, existing=existing, error=error, directions=DIRECTIONS,
                           stations=feed["stations"], stops=feed["stops"])


@app.route("/trip/<name>/delete", methods=["POST"])
def delete_trip(name):
    TripStore(USER).delete(name)
    return redirect(url_for("home"))


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))  # no traffic is sent; this just picks the Wi-Fi address
        return s.getsockname()[0]


if __name__ == "__main__":
    phone = "--phone" in sys.argv
    print("Loading today's schedule...")
    feed_for(date.today())
    print("Open http://localhost:8080 on this computer")
    if phone:
        print(f"On your phone (same Wi-Fi): http://{lan_ip()}:8080")
        print("Anyone on this Wi-Fi can open it too, so only use --phone on a network you trust.")
    app.run(host="0.0.0.0" if phone else "127.0.0.1", port=8080)
