"""FrontRunner Tool website: My trips on your phone.

Run:  .venv/bin/python app.py          → open http://localhost:8080 on this computer
      .venv/bin/python app.py --phone  → also reachable from your phone on the same Wi-Fi
"""
import re
import socket
from urllib.parse import urlparse
import sys
from datetime import date, datetime, timedelta

from flask import Flask, abort, redirect, render_template, request, url_for

from commute import TRANSFER, best_arrive_by, evening_plans, mins, morning_plans, short
from frontrunner import fmt, load_feed, rows, seconds
from planner import TRAIN, journeys, places
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
        _cache[day] = dict(date=day, fr=fr, uvx=uvx, stations=stop_names(fr), stops=stop_names(uvx),
                           places=places(fr, uvx))
    return _cache[day]


def stop_names(trips):
    """Sorted stop names served by these trips, without bay/platform suffixes and without Provo Central."""
    names = {re.sub(r" \(.*\)$", "", s[0]) for stops in trips for s in stops}
    return sorted(n for n in names if not n.startswith(TRANSFER))


def at(day, gtfs_time):
    """Real clock time for a schedule time on a service day (schedule times can pass 24:00)."""
    return datetime.combine(day, datetime.min.time()) + timedelta(seconds=seconds(gtfs_time))


PROVO = short(TRANSFER)  # "Provo Central"


def toward(headsign):
    """"To Provo" -> "Provo", "Orem Central Station" -> "Orem Central": the way riders name a direction."""
    return short(headsign.removeprefix("To "))


def ride(mode, board_at, board, alight_at, alight, headsign):
    """One ride on the timeline, e.g. FrontRunner, Murray Central 10:16 → Provo Central 11:11."""
    where = toward(headsign)
    if where.startswith("East Bay"):
        where = "East Bay loop"
    elif alight_at.startswith(where):
        where = None  # the ride ends where it's headed, so "toward" adds nothing
    else:
        where = f"toward {where}"
    return dict(mode=mode, name="FrontRunner" if mode == "train" else "UVX", toward=where,
                start=board_at, time=fmt(board), end=alight_at, arrive=fmt(alight))


def walk(leg_mode, minutes):
    return dict(mode="walk", minutes=minutes,
                to="the FrontRunner platform" if leg_mode == "train" else "the UVX stop")


def to_campus_option(trip, day, train, bus):
    """Train from home station to Provo Central, then UVX to campus."""
    home, campus = short(trip.station), short(trip.stop)
    transfer = mins(train[1], bus[0])
    return dict(
        leave=at(day, train[0]), time=fmt(train[0]), vehicle="train", start=home,
        dest=campus, arrive=fmt(bus[1]), total=mins(train[0], bus[1]), transfer=transfer,
        legs=[ride("train", home, train[0], PROVO, train[1], train[2]), walk("bus", transfer),
              ride("bus", PROVO, bus[0], campus, bus[1], bus[2])],
    )


def home_option(trip, day, bus, train):
    """UVX from campus to Provo Central, then train to home station."""
    home, campus = short(trip.station), short(trip.stop)
    transfer = mins(bus[1], train[0])
    return dict(
        leave=at(day, bus[0]), time=fmt(bus[0]), vehicle="UVX", start=campus,
        dest=home, arrive=fmt(train[1]), total=mins(bus[0], train[1]), transfer=transfer,
        legs=[ride("bus", campus, bus[0], PROVO, bus[1], bus[2]), walk("train", transfer),
              ride("train", PROVO, train[0], home, train[1], train[2])],
    )


def journey_option(journey, day):
    """A planned journey (planner.journeys) as a timeline, in the same shape as saved-trip options."""
    first, last = journey[0], journey[-1]
    legs = []
    for i, leg in enumerate(journey):
        mode = "train" if leg["mode"] == TRAIN else "bus"
        if i:
            legs.append(walk(mode, mins(journey[i - 1]["alight"], leg["board"])))
        legs.append(ride(mode, short(leg["board_at"]), leg["board"], short(leg["alight_at"]), leg["alight"],
                         leg["headsign"]))
    return dict(
        leave=at(day, first["board"]), arrive_at=at(day, last["alight"]), time=fmt(first["board"]),
        vehicle="train" if first["mode"] == TRAIN else "UVX", start=short(first["board_at"]),
        dest=short(last["alight_at"]), arrive=fmt(last["alight"]), total=mins(first["board"], last["alight"]),
        transfer=legs[1]["minutes"] if len(journey) > 1 else None, legs=legs,
    )


def planned_options(feed, start, end, walk=3):
    return [journey_option(j, feed["date"]) for j in journeys(feed["fr"], feed["uvx"], start, end, walk)]


def options(trip, feed):
    """Every way to make the trip on feed's day, in the order you'd leave. Schedule math is in commute.py
    (saved commutes) and planner.py (any other trip)."""
    day = feed["date"]
    if trip.direction == "any":
        return planned_options(feed, trip.start, trip.end, trip.walk)
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


def duration(minutes):
    """78 -> "1 h 18 min"."""
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def part_of_day(now):
    """Which kind of trip makes sense right now: to campus before noon, home after."""
    return "to-campus" if now.hour < 12 else "home"


@app.context_processor
def helpers():
    now = datetime.now()
    return dict(short=short, PROVO=PROVO, TIGHT=TIGHT, SOON=SOON, RUSH=RUSH, duration=duration,
                countdown=lambda leave: countdown(leave, now),
                rush_text=lambda leave: rush_text(leave, now),
                fmt_time=lambda hhmm: fmt(f"{hhmm}:00"),
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
    return render_template("index.html", cards=cards, saved=request.args.get("saved"))


@app.route("/trip/<name>")
def show_trip(name):
    trip = get_trip(name)
    now = datetime.now()

    arrive = request.args.get("arrive_by") or trip.arrive_by
    plan = None
    if trip.direction != "home" and arrive:
        if not re.fullmatch(r"\d{1,2}:\d{2}", arrive):
            abort(400)
        feed = feed_for(now.date())
        if trip.direction == "to-campus":
            best = best_arrive_by(morning_plans(feed["fr"], feed["uvx"], trip.station, trip.stop, trip.walk), arrive)
            option = to_campus_option(trip, now.date(), *best) if best else None
        else:
            option = latest_arriving_by(options(trip, feed), at(now.date(), f"{arrive}:00"))
        plan = dict(target=fmt(f"{arrive}:00"), option=option, gone=bool(option) and option["leave"] < now)

    return render_template("trip.html", trip=trip, v=trip_view(trip, now), arrive=arrive,
                           asked=bool(request.args.get("arrive_by")), plan=plan, today=now.date())


def latest_arriving_by(opts, deadline):
    """The option that leaves latest but still arrives by deadline, or None."""
    fits = [o for o in opts if o["arrive_at"] <= deadline]
    return fits[-1] if fits else None


def day_lead(day, today):
    """How the answer sentence starts: 'Catch', 'Tomorrow, catch' or 'On Friday, Oct 2, catch'."""
    if day == today:
        return "Catch"
    if day == today + timedelta(days=1):
        return "Tomorrow, catch"
    return f"On {day.strftime('%A, %b')} {day.day}, catch"


def schedule_dates():
    """First and last day UTA's downloaded schedule covers."""
    info = next(rows(load_feed(), "feed_info.txt"))
    parse = lambda d: datetime.strptime(d, "%Y%m%d").date()
    return parse(info["feed_start_date"]), parse(info["feed_end_date"])


def read_plan_form(args, today):
    """Check the planner inputs. Returns (inputs, error)."""
    stations, stops = feed_for(today)["places"]
    p = dict(start=args.get("from", ""), end=args.get("to", ""), when=args.get("when", "now"),
             time=args.get("time", ""), day=args.get("day", "today"), date=args.get("date", ""))
    if p["start"] not in stations + stops or p["end"] not in stations + stops:
        return p, "Choose where you're starting and where you're going."
    if p["start"] == p["end"]:
        return p, "Your start and destination are the same."
    if p["when"] not in ("now", "leave", "arrive"):
        p["when"] = "now"
    if p["when"] != "now" and not re.fullmatch(r"\d{1,2}:\d{2}", p["time"]):
        return p, "Pick a time."
    if p["when"] == "now" or p["day"] == "today":
        p["day"], p["on"] = "today", today
    elif p["day"] == "tomorrow":
        p["on"] = today + timedelta(days=1)
    else:
        try:
            p["on"] = datetime.strptime(p["date"], "%Y-%m-%d").date()
        except ValueError:
            return p, "Pick a date."
        if p["on"] < today:
            return p, "Pick today or a later date."
    first, last = schedule_dates()
    if not first <= p["on"] <= last:
        return p, (f"The schedule this tool has covers {first:%b} {first.day} to {last:%b} {last.day}, {last.year}. "
                   "Pick a date in that range.")
    return p, None


@app.route("/plan")
def plan_trip():
    now = datetime.now()
    today = now.date()
    stations, stops = feed_for(today)["places"]
    if "from" not in request.args or "change" in request.args:
        p, _ = read_plan_form(request.args, today)
        return render_template("plan.html", p=p, stations=stations, stops=stops, error=None)
    p, error = read_plan_form(request.args, today)
    if error:
        return render_template("plan.html", p=p, stations=stations, stops=stops, error=error)

    day = p["on"]
    opts = planned_options(feed_for(day), p["start"], p["end"])
    r = dict(main=None, lead=day_lead(day, today), rush=False, backup=None, backup_lead=None,
             others=[], others_title="Later options", note=None, gone=None)

    if p["when"] == "arrive":
        deadline = at(day, f"{p['time']}:00")
        fits = [o for o in opts if o["arrive_at"] <= deadline]
        if not fits:
            r["note"] = f"No trip gets you to {short(p['end'])} by {fmt(p['time'] + ':00')} that day."
        elif fits[-1]["leave"] < now:
            r["gone"] = fits[-1]
        else:
            r["main"] = fits[-1]
            r["others"] = [o for o in reversed(fits[:-1]) if o["leave"] >= now][:5]
            r["others_title"] = "Earlier options that also get you there in time"
    else:
        start = now if p["when"] == "now" else at(day, f"{p['time']}:00")
        ahead = [o for o in opts if o["leave"] >= start]
        if ahead:
            r["main"], r["others"] = ahead[0], ahead[1:]
        else:
            nxt = day + timedelta(days=1)
            later = planned_options(feed_for(nxt), p["start"], p["end"]) if nxt <= schedule_dates()[1] else []
            r["note"] = "Nothing else leaves that day." if p["when"] == "leave" else "The last one today has left."
            if later:
                r["main"], r["lead"] = later[0], day_lead(nxt, today)

    main = r["main"]
    if main and main["leave"].date() == today and main["leave"] - now < timedelta(minutes=RUSH) \
            and p["when"] != "arrive":
        r["rush"] = True
        if r["others"]:
            r["backup"], r["others"] = r["others"][0], r["others"][1:]
            r["backup_lead"] = "Catch"

    save = url_for("save_plan", start=p["start"], end=p["end"],
                   **({"arrive_by": p["time"]} if p["when"] == "arrive" else {}))
    return render_template("plan_result.html", p=p, r=r, today=today, save_url=save,
                           change_url=url_for("plan_trip", change=1, **{k: v for k, v in request.args.items()
                                                                        if k != "change"}))


@app.route("/plan/save", methods=["GET", "POST"])
def save_plan():
    """"Save to My trips" from planner results: just ask for a name, then go home."""
    src = request.form if request.method == "POST" else request.args
    start, end = src.get("start", ""), src.get("end", "")
    trip = Trip(src.get("name", f"{short(start)} to {short(end)}").strip(), "any", "", "",
                arrive_by=src.get("arrive_by") or None, start=start, end=end)
    error = None
    if request.method == "POST":
        feed = feed_for(date.today())
        try:
            validate(trip, feed["fr"], feed["uvx"])
            TripStore(USER).save(trip)
            return redirect(url_for("home", saved=trip.name))
        except ValueError as err:
            error = str(err)
    return render_template("save.html", trip=trip, error=error,
                           back=planner_page(src.get("back") or request.referrer))


def planner_page(link):
    """The planner results page this link points to on this site, or the planner form."""
    u = urlparse(link or "")
    if u.path == url_for("plan_trip") and u.query and (not u.netloc or u.netloc == request.host):
        return f"{u.path}?{u.query}"
    return url_for("plan_trip")


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
                    f.get("stop", ""), walk, f.get("arrive_by") or None, f.get("start"), f.get("end"))
        try:
            if trip.direction == "home":
                trip.arrive_by = None
            validate(trip, feed["fr"], feed["uvx"])
            TripStore(USER).save(trip, replacing=existing.name if existing else None)
            return redirect(url_for("show_trip", name=trip.name))
        except ValueError as err:
            error = str(err)
    return render_template("form.html", trip=trip, existing=existing, error=error, directions=DIRECTIONS,
                           stations=feed["stations"], stops=feed["stops"], places=feed["places"])


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
