"""Parsing helpers: US states, locations, salaries and work model from posting text."""
from __future__ import annotations

import re
from datetime import datetime, timezone

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
CODES = set(STATES.values())
_STATE_NAME_RE = re.compile(r"\b(" + "|".join(sorted(STATES, key=len, reverse=True)) + r")\b", re.I)

NON_US = re.compile(
    r"\b(canada|united kingdom|\buk\b|india|mexico|germany|ireland|poland|philippines|brazil|"
    r"australia|singapore|netherlands|spain|portugal|romania|argentina|colombia|costa rica|"
    r"toronto|london|bengaluru|bangalore|hyderabad|pune|chennai|dublin|krakow|manila)\b",
    re.I,
)


def state_code(s: str | None) -> str | None:
    if not s:
        return None
    t = s.strip()
    if t.upper() in CODES:
        return t.upper()
    return STATES.get(t.lower())


METRO_ALIASES = {   # region names LinkedIn uses instead of "City, ST" -> (city, state)
    "new york city metropolitan area": ("New York", "NY"),
    "greater new york city area": ("New York", "NY"),
    "new york metropolitan area": ("New York", "NY"),
    "san francisco bay area": ("San Francisco", "CA"),
    "los angeles metropolitan area": ("Los Angeles", "CA"),
    "greater los angeles": ("Los Angeles", "CA"),
    "san diego metropolitan area": ("San Diego", "CA"),
    "greater sacramento": ("Sacramento", "CA"),
    "greater chicago area": ("Chicago", "IL"),
    "chicago metropolitan area": ("Chicago", "IL"),
    "greater boston": ("Boston", "MA"),
    "boston metropolitan area": ("Boston", "MA"),
    "greater seattle area": ("Seattle", "WA"),
    "seattle metropolitan area": ("Seattle", "WA"),
    "washington dc-baltimore area": ("Washington", "DC"),
    "washington metropolitan area": ("Washington", "DC"),
    "dallas-fort worth metroplex": ("Dallas", "TX"),
    "greater houston": ("Houston", "TX"),
    "austin, texas metropolitan area": ("Austin", "TX"),
    "greater austin area": ("Austin", "TX"),
    "atlanta metropolitan area": ("Atlanta", "GA"),
    "greater atlanta area": ("Atlanta", "GA"),
    "denver metropolitan area": ("Denver", "CO"),
    "greater denver area": ("Denver", "CO"),
    "greater phoenix area": ("Phoenix", "AZ"),
    "miami-fort lauderdale area": ("Miami", "FL"),
    "greater tampa bay area": ("Tampa", "FL"),
    "greater orlando": ("Orlando", "FL"),
    "greater minneapolis-st. paul area": ("Minneapolis", "MN"),
    "detroit metropolitan area": ("Detroit", "MI"),
    "greater philadelphia": ("Philadelphia", "PA"),
    "philadelphia metropolitan area": ("Philadelphia", "PA"),
    "greater philadelphia area": ("Philadelphia", "PA"),
    "greater pittsburgh region": ("Pittsburgh", "PA"),
    "charlotte metro": ("Charlotte", "NC"),
    "raleigh-durham-chapel hill area": ("Raleigh", "NC"),
    "nashville metropolitan area": ("Nashville", "TN"),
    "greater st. louis": ("St. Louis", "MO"),
    "kansas city metropolitan area": ("Kansas City", "MO"),
    "salt lake city metropolitan area": ("Salt Lake City", "UT"),
    "portland, oregon metropolitan area": ("Portland", "OR"),
    "greater cleveland": ("Cleveland", "OH"),
    "columbus, ohio metropolitan area": ("Columbus", "OH"),
    "cincinnati metropolitan area": ("Cincinnati", "OH"),
    "greater indianapolis": ("Indianapolis", "IN"),
    "las vegas metropolitan area": ("Las Vegas", "NV"),
}


def parse_location(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """'Oak Park, IL' / 'Chicago, Illinois, United States' -> (city, state, country)."""
    if not raw:
        return None, None, None
    text = raw.strip()
    alias = METRO_ALIASES.get(re.sub(r"\s+", " ", text.lower()).strip())
    if alias:
        return alias[0], alias[1], "US"
    parts = [p.strip() for p in re.split(r"[,;|/]| - ", text) if p.strip()]
    city = state = None
    # Scan from the right so "New York, NY" and "Washington, DC" read the last part as the state
    # and the part before it as the city (a left-to-right scan took "New York" as the state and lost the city).
    for i in range(len(parts) - 1, -1, -1):
        p = parts[i]
        code = state_code(p) or (state_code(p.split()[0]) if p.split() and len(p.split()[0]) == 2 else None)
        if code:
            state = code
            if i > 0:
                city = _clean_city(parts[i - 1])
            break
    us_named = bool(re.search(r"\b(united states|usa|u\.s\.a?\.?|us)\b", text, re.I))
    if not state and not us_named and NON_US.search(text):
        return None, None, "NON_US"
    if not state:
        m = _STATE_NAME_RE.search(text)
        if m:
            state = STATES[m.group(1).lower()]
    if not state and NON_US.search(text):      # "London, United States" style oddities: trust the non-US place
        return None, None, "NON_US"
    country = "US" if state or us_named else None
    if city and re.fullmatch(r"(remote|hybrid|united states|usa|us)", city, re.I):
        city = None
    return city, state, country


def _clean_city(s: str) -> str | None:
    s = re.sub(r"\b(remote|hybrid|on-?site|office)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -()")
    return s or None


# ---------- work model ----------
_HYBRID = re.compile(
    r"\bhybrid\b|\b(\d|one|two|three|four)\s*(\+\s*)?days?\s*(a|per|each)\s*week\b.{0,40}\b(office|on-?site|in person)"
    r"|\b(office|on-?site|in person)\b.{0,40}\b(\d|one|two|three|four)\s*days?\s*(a|per|each)\s*week",
    re.I,
)
_REMOTE = re.compile(r"\b(fully|100%|completely)?\s*remote\b|\bwork from home\b|\bwfh\b|\bremote-first\b", re.I)
_NOT_REMOTE = re.compile(r"\bnot (a )?remote\b|\bno remote\b|\bremote work is not\b|\bthis is not a remote\b", re.I)
_ONSITE = re.compile(r"\b(fully|100%)\s*on-?site\b|\bon-?site\s*(5|five)\s*days\b|\bin[- ]office\s*(5|five)\s*days\b", re.I)


# "hybrid" as a work arrangement, not as technology ("hybrid cloud", "hybrid integration platform").
_HYBRID_BODY = re.compile(
    r"\b(\d|one|two|three|four)\s*(\+\s*)?days?\s*(a|per|each)\s*week\b.{0,40}\b(office|on-?site|in person)"
    r"|\b(office|on-?site|in person)\b.{0,40}\b(\d|one|two|three|four)\s*days?\s*(a|per|each)\s*week"
    r"|\bhybrid\s*[:(-]?\s*(work|working|schedule|role|position|model|arrangement|environment|basis|opportunity|"
    r"remote|on-?site|in[- ]office|\d|one|two|three|four)\b"
    r"|\b(role|position|job|schedule|work|opportunity|this)\s+(is|will be|is a|is an)\s+(a\s+)?hybrid\b"
    r"|\bhybrid\b.{0,25}\b(office|on-?site|in person|days?)\b"
    r"|\b(work model|work arrangement|workplace type|work type|location type|work location)\s*[:\-]?\s*hybrid\b"
    r"|(?:^|[\n.:;(])\s*hybrid\s*(?:[.,;)\n]|$)",
    re.I,
)


def detect_work_model(title: str, location_raw: str, description: str, is_remote: bool | None,
                      workplace_hint: str | None) -> str:
    """Returns remote, hybrid, onsite. A clear statement in the posting beats the source's tag."""
    head = f"{title} {location_raw}"
    body = description or ""
    hint = (workplace_hint or "").lower()
    if _HYBRID.search(head) or "hybrid" in hint:
        return "hybrid"
    if _ONSITE.search(head) or hint in {"onsite", "on-site", "on_site", "in office"}:
        return "onsite"
    if _NOT_REMOTE.search(body):
        return "hybrid" if _HYBRID_BODY.search(body) else "onsite"
    if _HYBRID_BODY.search(body):
        return "hybrid"
    if is_remote or "remote" in hint or _REMOTE.search(head):
        return "remote"
    if _REMOTE.search(body[:1500]) and not _ONSITE.search(body):
        return "remote"
    return "onsite"


_RESIDENCY = re.compile(
    r"(must|need to|required to|should)\s+(reside|live|be located|be based)\s+(in|within)\s+(?P<where>[^.\n]{2,160})",
    re.I,
)


def residency_states(description: str) -> set[str] | None:
    """States a remote posting restricts candidates to, or None if unrestricted."""
    out: set[str] = set()
    for m in _RESIDENCY.finditer(description or ""):
        where = m.group("where")
        if re.search(r"\b(united states|us|u\.s\.|usa|the country)\b", where, re.I) and not _STATE_NAME_RE.search(where):
            continue
        out.update(STATES[n.lower()] for n in _STATE_NAME_RE.findall(where))
        out.update(c for c in re.findall(r"\b([A-Z]{2})\b", where) if c in CODES)
    return out or None


# ---------- salary ----------
_MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?([kK])?"
_UNIT = r"(?:\s*(?:/|per|an|a)\s*(?:hour|hr|year|yr|annum|annually))?"
_RANGE = re.compile(_MONEY + _UNIT + r"\s*(?:-|to|–|—|and)\s*" + _MONEY + r"(?P<per>\s*(?:/|per|an|a)\s*(?:hour|hr|year|yr|annum|annually))?", re.I)
_SINGLE = re.compile(_MONEY + r"(?P<per>\s*(?:/|per|an|a)\s*(?:hour|hr|year|yr|annum|annually))?", re.I)

PERIOD_MULT = {"HOUR": 2080, "DAY": 260, "WEEK": 52, "MONTH": 12, "YEAR": 1}


def _amount(num: str, k: str | None) -> float:
    v = float(num.replace(",", ""))
    return v * 1000 if k else v


def annualize(v: float | None, period: str | None) -> float | None:
    if v is None:
        return None
    p = (period or "").upper()
    if p in PERIOD_MULT:
        return v * PERIOD_MULT[p]
    return v * 2080 if v < 300 else v      # bare small numbers are hourly rates


def parse_salary_text(text: str | None) -> tuple[float | None, float | None]:
    """Find a pay range in posting text and return annual USD (min, max)."""
    if not text:
        return None, None
    best = None   # several ranges (bonus, other regions): keep the one with the highest top
    for m in _RANGE.finditer(text):
        lo, hi = _amount(m.group(1), m.group(2)), _amount(m.group(3), m.group(4))
        hourly = bool(m.group("per") and re.search(r"hour|hr", m.group("per"), re.I))
        if hourly or hi < 300:
            lo, hi = lo * 2080, hi * 2080
        if 30000 <= lo <= hi <= 1_500_000 and (best is None or hi > best[1]):
            best = (lo, hi)
    if best:
        return best
    for m in _SINGLE.finditer(text):
        v = _amount(m.group(1), m.group(2))
        per = m.group("per") or ""
        if re.search(r"hour|hr", per, re.I) and 20 <= v <= 400:
            return v * 2080, v * 2080
        if re.search(r"year|yr|annum|annual", per, re.I) and 30000 <= v <= 1_500_000:
            return v, v
    return None, None


def parse_dt(v) -> datetime | None:
    """ISO strings, epoch seconds or epoch milliseconds to aware UTC datetime."""
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            ts = v / 1000 if v > 10**11 else v
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(v).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    import html as h
    text = h.unescape(html)
    text = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = h.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()
