"""Decide whether a job's place and work model fit the search area.

The search area comes from config.yaml (location.local_areas). An area is either a whole state:

    - {name: Illinois, state: IL}

or a circle around a point, with the towns inside it listed for postings that carry no coordinates:

    - {name: Chicago, center: [41.8781, -87.6298], radius_mi: 30, in_state: IL,
       towns: [chicago, evanston, oak park, naperville]}
"""
from __future__ import annotations

import math

from .models import Job
from .textparse import residency_states


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def _states(area: dict) -> set[str]:
    v = area.get("in_state") or []
    return {v} if isinstance(v, str) else set(v)


def local_area(job: Job, areas: list[dict]) -> str | None:
    """Name of the configured area the job sits in, or None."""
    for area in areas:
        if "center" not in area and area.get("state") and job.state == area["state"]:
            return area["name"]
    circles = [a for a in areas if "center" in a]
    if job.lat is not None and job.lon is not None:
        for area in circles:
            if miles((job.lat, job.lon), tuple(area["center"])) <= area["radius_mi"]:
                return area["name"]
        return None
    city = (job.city or "").lower().strip()
    if not city:
        return None
    for area in circles:
        towns = {str(t).lower().strip() for t in area.get("towns") or []}
        states = _states(area)
        if city in towns and (not job.state or not states or job.state in states):
            return area["name"]
    return None


def location_verdict(job: Job, work_model: str, cfg: dict) -> tuple[str, str]:
    """Returns (verdict, detail). verdict is pass, fail or unclear (unclear goes to Claude to decide)."""
    loc = cfg["location"]
    if work_model not in loc["work_models"]:
        return "fail", f"{work_model} not wanted"
    if job.country == "NON_US":
        return "fail", "outside the US"

    if work_model == "remote":
        allowed = set(loc["remote"].get("allowed_residency_states", []))
        restricted = residency_states(job.description)
        if restricted and allowed and not (restricted & allowed):
            return "fail", "remote but must live in " + ", ".join(sorted(restricted))
        return "pass", "Remote US"

    areas = loc.get("local_areas") or []
    area = local_area(job, areas)
    if area:
        return "pass", f"{work_model.capitalize()}, {area}"
    circle_states = set().union(*[_states(a) for a in areas if "center" in a]) if areas else set()
    if job.state in circle_states and not job.city and job.lat is None:
        # Only the state is known ("New York, United States"): it could be inside a circle; Claude decides.
        return "unclear", f"{work_model}, {job.state} (town not stated)"
    if job.state or (job.lat is not None):
        return "fail", f"{work_model} outside the search area ({job.city or ''} {job.state or ''})".strip()
    return "unclear", f"{work_model}, location not stated"
