"""End-to-end and unit tests with mocked job APIs and a fake scorer (no network, no API keys)."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures"      # sample search the tests run against
NOW = datetime(2026, 11, 2, 12, 5, tzinfo=timezone.utc)


def iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def js(i, title, employer, city=None, state=None, remote=False, lat=None, lon=None, smin=None, smax=None,
       period=None, desc="Design integration services with Kafka, MuleSoft and AWS.", hours_ago=3, publisher="LinkedIn",
       country="US"):
    return {"job_id": f"js{i}", "job_title": title, "employer_name": employer, "job_city": city, "job_state": state,
            "job_country": country, "job_location": ", ".join(x for x in [city, state] if x), "job_is_remote": remote,
            "job_latitude": lat, "job_longitude": lon, "job_min_salary": smin, "job_max_salary": smax,
            "job_salary_period": period, "job_description": desc, "job_posted_at_datetime_utc": iso(hours_ago),
            "job_apply_link": f"https://example.com/apply/{i}", "job_publisher": publisher}


JSEARCH = [
    js(1, "Integration Architect", "Prudential Financial", "Newark", "NJ", smin=150000, smax=190000, period="YEAR",
       desc="Hybrid, 3 days per week in our Newark office. Kafka, APIs, AWS."),
    js(2, "Senior Integration Architect", "Lone Star Co", remote=True,
       desc="Fully remote. Candidates must reside in Texas or Oklahoma."),
    js(3, "Enterprise Integration Architect", "Peach Corp", "Atlanta", "GA", lat=33.75, lon=-84.39),
    js(4, "Junior Integration Developer", "Small Co", "Princeton", "NJ"),
    js(5, "Integration Lead", "Big Bank", "New York", "NY", lat=40.75, lon=-73.99, smin=120000, smax=135000, period="YEAR"),
    js(6, "Integration Architect (Remote)", "Carolina Bank", "Charlotte", "NC", remote=True,
       desc="This role is hybrid: 3 days per week in our Charlotte, NC office."),
    js(7, "Agentic AI Architect", "Pharma Inc", "King of Prussia", "PA", desc="Build agent platforms with MCP."),
    js(8, "Integration Architect", "Prudential Financial, Inc.", "Newark", "NJ", publisher="Indeed",
       desc="Hybrid, 3 days per week in our Newark office."),
    js(9, "MuleSoft Integration Architect (Contract)", "Staffing LLC", remote=True, smin=85, smax=85, period="HOUR",
       desc="100% remote contract. Must be authorized to work in the United States."),
    js(10, "Integration Architect", "Old Post Co", "Edison", "NJ", hours_ago=200),   # outside the 7-day first-run window
    js(11, "Integration Architect", "Hudson Valley Co", "Albany", "NY", lat=42.65, lon=-73.75),
    js(12, "Middleware Architect", "Westchester Insurance", "Purchase", "NY", desc="Onsite five days. IBM MQ and ACE."),
    js(13, "Solutions Architect", "Garden State Bank", "Jersey City", "NJ",
       desc="Hybrid. You will design payment integrations on MuleSoft and Kafka, deployed on AWS, and own the "
            "API standards for our digital banking channels."),
    js(14, "Salesforce Architect", "CRM Co", remote=True,
       desc="Fully remote. Own Salesforce Sales Cloud and Service Cloud design, Apex and Lightning components, "
            "data model, release management and CI/CD for the CRM team."),
    js(15, "Integration Architect", "Defense Co", "Philadelphia", "PA",
       desc="Onsite. Design integrations with MuleSoft and Kafka on AWS. An active Secret clearance is required "
            "for this position. US citizenship needed."),
    js(16, "Principal Engineer, Platform", "Fintech Co", "New York", "NY", lat=40.71, lon=-74.01,
       desc="Hybrid in Manhattan. Build event streaming on Kafka, an API gateway layer and REST APIs on AWS. "
            "Boomi experience is a plus but not needed."),
]

GREENHOUSE = {"jobs": [
    {"id": 101, "title": "Staff Integration Engineer", "location": {"name": "Remote - US"},
     "absolute_url": "https://boards.greenhouse.io/acme/jobs/101", "first_published": iso(5),
     "content": "&lt;p&gt;Remote across the US. Salary $175,000 - $210,000 per year.&lt;/p&gt;"},
    {"id": 102, "title": "Solutions Architect", "location": {"name": "London, United Kingdom"},
     "absolute_url": "https://boards.greenhouse.io/acme/jobs/102", "first_published": iso(5), "content": "UK role"},
]}
LEVER = [{"id": "lv1", "text": "Principal Architect, Integrations", "hostedUrl": "https://jobs.lever.co/beta/lv1",
          "categories": {"location": "Philadelphia, PA"}, "workplaceType": "hybrid",
          "salaryRange": {"min": 180000, "max": 220000, "currency": "USD", "interval": "per-year-salary"},
          "descriptionPlain": "Lead integration architecture.", "createdAt": int((NOW - timedelta(hours=10)).timestamp() * 1000)}]
ASHBY = {"jobs": [{"id": "as1", "title": "AI Platform Engineer, MCP", "location": "United States", "isRemote": True,
                   "workplaceType": "Remote", "jobUrl": "https://jobs.ashbyhq.com/gamma/as1", "isListed": True,
                   "descriptionPlain": "Build MCP servers.", "publishedAt": iso(2),
                   "compensation": {"compensationTierSummary": "$170K – $210K"}}]}
WD_LIST = {"jobPostings": [
    {"title": "Integration Architect", "externalPath": "/job/Wilmington-DE/Integration-Architect_R1", "locationsText": "Wilmington, DE", "postedOn": "Posted Today"},
    {"title": "Store Associate", "externalPath": "/job/Newark-DE/Store-Associate_R2", "locationsText": "Newark, DE", "postedOn": "Posted Today"},
]}
WD_DETAIL = {"jobPostingInfo": {"title": "Integration Architect", "location": "Wilmington, DE", "remoteType": "Hybrid",
                                "jobDescription": "<p>Design APIs.</p><p>Pay range $145,000 to $185,000</p>",
                                "jobReqId": "R1", "externalUrl": "https://delta.wd5.myworkdayjobs.com/en-US/Ext/job/R1"}}


DICE = [
    {"id": "d1", "title": "MuleSoft Integration Architect", "companyName": "Apex Systems", "jobLocation": {"displayName": "Remote"},
     "detailsPageUrl": "https://www.dice.com/job-detail/d1", "salary": "$85 - $95 per hour", "employmentType": "THIRD_PARTY",
     "workplaceTypes": ["Remote"], "isRemote": True, "postedDate": iso(4),
     "summary": "12 month contract, corp-to-corp accepted. MuleSoft, IBM MQ, REST API and AWS integration for a retail client. " * 3},
    {"id": "d2", "title": "Integration Engineer", "companyName": "Garden State Health Plan", "jobLocation": {"displayName": "Trenton, NJ"},
     "detailsPageUrl": "https://www.dice.com/job-detail/d2", "salary": "", "employmentType": "FULLTIME",
     "workplaceTypes": ["Hybrid"], "postedDate": iso(6), "summary": "Short snippet only."},
    {"id": "d3", "title": "Integration Engineer", "companyName": "Weekend Co", "jobLocation": {"displayName": "Remote"},
     "detailsPageUrl": "https://www.dice.com/job-detail/d3", "employmentType": "PARTTIME",
     "workplaceTypes": ["Remote"], "isRemote": True, "postedDate": iso(6), "summary": "Part time."},
]
DICE_CALLS: list[dict] = []


def dice_reply(request: httpx.Request) -> httpx.Response:
    msg = json.loads(request.content)
    if "id" not in msg:
        return httpx.Response(202)
    if msg["method"] == "initialize":
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}},
                              headers={"Mcp-Session-Id": "s1"})
    args = msg["params"]["arguments"]
    DICE_CALLS.append(args)
    assert request.headers.get("mcp-session-id") == "s1"
    hit = args["keyword"] == "integration architect" and args.get("workplace_types") == ["Remote"]
    payload = {"data": DICE if hit else [], "meta": {"currentPage": 1, "pageCount": 1}}
    result = {"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
    # Dice may answer as a server-sent event stream; the client must read both forms
    return httpx.Response(200, text="event: message\ndata: " + json.dumps(result) + "\n\n",
                          headers={"content-type": "text/event-stream"})


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "mcp.dice.com" in url:
        return dice_reply(request)
    if "jsearch" in url:
        q = request.url.params.get("query", "")
        return httpx.Response(200, json={"data": JSEARCH if q.startswith("integration architect in New Jersey") else []})
    if "greenhouse" in url:
        return httpx.Response(200, json=GREENHOUSE)
    if "lever" in url:
        return httpx.Response(200, json=LEVER)
    if "ashby" in url:
        return httpx.Response(200, json=ASHBY)
    if "myworkdayjobs" in url and request.method == "POST":
        return httpx.Response(200, json=WD_LIST)
    if "myworkdayjobs" in url:
        return httpx.Response(200, json=WD_DETAIL)
    return httpx.Response(404)


class FakeScorer:
    def __init__(self):
        self.triaged, self.assessed = [], []

    def triage(self, row):
        self.triaged.append(row["title"])
        low = "Contract" in row["title"]
        return {"score": 4 if low else 8, "reason": "test", "location_check": "remote_us" if row["work_model"] == "remote" else "local",
                "pay_below_floor": False}

    def assess(self, row):
        self.assessed.append(row["title"])
        strong = "Principal" in row["title"] or "Prudential" in row["company"]
        return {"summary": f"{row['title']} role", "keyword_match_pct": 90 if strong else 70,
                "hiring_manager_fit_pct": 85 if strong else 65, "seniority_pay_pct": 90, "verdict": "apply" if strong else "stretch",
                "top_gaps": ["gap a"], "hard_gaps": [], "pitch": "Kafka and MCP in production",
                "location_check": "remote_us" if row["work_model"] == "remote" else "local",
                "work_model": row["work_model"], "pay_top_annual_usd": None}


@pytest.fixture
def home(tmp_path, monkeypatch):
    shutil.copy(FIX / "config.yaml", tmp_path / "config.yaml")
    shutil.copy(REPO / "feedback.yaml", tmp_path / "feedback.yaml")
    cfg = (tmp_path / "config.yaml").read_text()
    # These tests exercise the optional catch-up and new-company backfill; the shipped default (a fixed
    # 24-hour window) has its own test below.
    for old, new in [("instant_min_score: null", "instant_min_score: 8.0"), ("pause_seconds: 1 ", "pause_seconds: 0 "),
                     ("catch_up: false", "catch_up: true"), ("new_company_backfill: false", "new_company_backfill: true")]:
        assert old in cfg, old
        cfg = cfg.replace(old, new)
    (tmp_path / "config.yaml").write_text(cfg)
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    DICE_CALLS.clear()
    (tmp_path / "profile").mkdir()
    shutil.copy(FIX / "profile.md", tmp_path / "profile" / "profile.md")
    (tmp_path / "companies.yaml").write_text(
        "companies:\n"
        "  - {name: Acme, ats: greenhouse, token: acme}\n"
        "  - {name: Beta, ats: lever, token: beta}\n"
        "  - {name: Gamma, ats: ashby, token: gamma}\n"
        "  - {name: Delta, ats: workday, host: delta.wd5.myworkdayjobs.com, tenant: delta, site: Ext}\n")
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    monkeypatch.setenv("JSEARCH_API_KEY", "test")
    monkeypatch.delenv("GMAIL_USER", raising=False)
    import importlib
    import jobradar.pipeline as pl
    importlib.reload(pl)
    return tmp_path, pl


def test_end_to_end(home):
    tmp, pl = home
    scorer = FakeScorer()
    run = pl.Run(dry_run=True, client=httpx.Client(transport=httpx.MockTransport(handler)), scorer=scorer, now=NOW)
    counts = run.execute()
    rows = {(r["title"], r["company"]): r for r in run.store.rows("1=1")}
    status = {k: (r["status"], r["filter_reason"]) for k, r in rows.items()}
    for k, v in sorted(status.items()):
        print(k, v)

    # dedupe: Prudential posted twice (LinkedIn + Indeed) is stored once
    assert sum(1 for k in rows if k[0] == "Integration Architect" and "Prudential" in k[1]) == 1
    assert status[("Senior Integration Architect", "Lone Star Co")][0] == "filtered"            # residency TX/OK
    assert "outside the search area" in status[("Enterprise Integration Architect", "Peach Corp")][1]
    assert status[("Junior Integration Developer", "Small Co")][1] == "title excluded"
    assert status[("Integration Lead", "Big Bank")][0] == "scored"                                # pay is no longer a filter
    assert "$120,000 to $135,000" in rows[("Integration Lead", "Big Bank")]["salary_note"]
    assert status[("Integration Architect (Remote)", "Carolina Bank")][0] == "filtered"           # mislabeled remote
    assert "older than" in status[("Integration Architect", "Old Post Co")][1]
    assert status[("Integration Architect", "Hudson Valley Co")][0] == "filtered"                 # Albany, outside 25 mi
    assert status[("Solutions Architect", "Acme")][1] == "outside the US"
    assert ("Store Associate", "Delta") not in rows                                               # title filter before detail call
    for k in [("Agentic AI Architect", "Pharma Inc"), ("Middleware Architect", "Westchester Insurance"),
              ("Staff Integration Engineer", "Acme"), ("Principal Architect, Integrations", "Beta"),
              ("AI Platform Engineer, MCP", "Gamma"), ("Integration Architect", "Delta")]:
        assert status[k][0] == "scored", k
    assert status[("MuleSoft Integration Architect (Contract)", "Staffing LLC")][0] == "triaged_low"
    assert rows[("MuleSoft Integration Architect (Contract)", "Staffing LLC")]["employment_type"] == "Contract"
    # Dice: corp-to-corp contract kept and labeled, short snippet passed to Claude, part-time dropped
    c2c = rows[("MuleSoft Integration Architect", "Apex Systems")]
    assert (c2c["status"], c2c["employment_type"], c2c["publisher"]) == ("scored", "Contract (C2C)", "Dice")
    assert c2c["salary_min"] == 85 * 2080
    assert status[("Integration Engineer", "Garden State Health Plan")][0] == "scored"
    assert rows[("Integration Engineer", "Garden State Health Plan")]["employment_type"] == "Full-time"
    assert "employment type not wanted" in status[("Integration Engineer", "Weekend Co")][1]
    first = DICE_CALLS[0]
    assert first["employment_types"] == ["FULLTIME", "CONTRACTS", "THIRD_PARTY"] and first["posted_date"] == "SEVEN"
    assert any(c.get("location") == "New York, NY" and c.get("radius") == 25 for c in DICE_CALLS)
    # skills check
    assert status[("Solutions Architect", "Garden State Bank")][0] == "scored"
    assert "MuleSoft" in rows[("Solutions Architect", "Garden State Bank")]["skills"]
    assert status[("Salesforce Architect", "CRM Co")][1].startswith("skills check")
    assert status[("Integration Architect", "Defense Co")][1] == "requires secret clearance"
    assert status[("Principal Engineer, Platform", "Fintech Co")][0] == "scored"     # Boomi only "a plus"

    assert rows[("Middleware Architect", "Westchester Insurance")]["work_model"] == "onsite"
    assert rows[("Integration Architect", "Delta")]["location_label"] == "Hybrid, Delaware"
    assert rows[("Integration Architect", "Delta")]["salary_min"] == 145000
    assert rows[("AI Platform Engineer, MCP", "Gamma")]["salary_max"] == 210000
    assert rows[("Principal Architect, Integrations", "Beta")]["rank"] >= 8.0
    assert counts["alerted"] >= 2
    assert (tmp / "out" / "emails" / "alert.html").exists()
    assert (tmp / "out" / "emails" / "digest.html").exists()        # 7 a.m. local
    assert (tmp / "out" / "ranked.csv").exists()

    # second run: nothing new, nothing re-alerted, digest not repeated
    run2 = pl.Run(dry_run=True, client=httpx.Client(transport=httpx.MockTransport(handler)), scorer=FakeScorer(),
                  now=NOW + timedelta(minutes=50))
    c2 = run2.execute()
    assert c2["new"] == 0 and c2["alerted"] == 0
    assert run2.store.rows("status='scored' AND digested_at IS NULL") == []


def test_feedback_and_jsearch_every_hours(home):
    tmp, pl = home
    cfg = (tmp / "config.yaml").read_text().replace("every_hours: 1 ", "every_hours: 2 ")
    (tmp / "config.yaml").write_text(cfg)
    run = pl.Run(dry_run=True, client=httpx.Client(transport=httpx.MockTransport(handler)), scorer=FakeScorer(), now=NOW)
    run.execute()
    import yaml
    jsc = yaml.safe_load(cfg)["sources"]["jsearch"]
    assert run.counts["jsearch_calls"] == len(jsc["queries"]) * len(jsc["locations"]) * jsc["first_run_pages"]
    (tmp / "feedback.yaml").write_text("applied:\n  - https://jobs.lever.co/beta/lv1\nnot_interested: []\nblocked_companies: []\n")
    run2 = pl.Run(dry_run=True, client=httpx.Client(transport=httpx.MockTransport(handler)), scorer=FakeScorer(),
                  now=NOW + timedelta(hours=1))
    run2.execute()
    assert run2.counts["jsearch_calls"] == 0       # not due yet
    assert run2.store.rows("url='https://jobs.lever.co/beta/lv1'")[0]["status"] == "applied"


def test_sources_keep_their_own_clock_and_new_companies_backfill(home):
    tmp, pl = home
    client = httpx.Client(transport=httpx.MockTransport(handler))
    run = pl.Run(dry_run=True, client=client, scorer=FakeScorer(), now=NOW)
    run.execute()
    # LinkedIn had no token, so it never succeeded; JSearch and Dice did
    later = NOW + timedelta(days=3)
    run2 = pl.Run(dry_run=True, client=client, scorer=FakeScorer(), now=later)
    assert run2.lookback("jsearch") == (False, 73)
    assert run2.lookback("dice") == (False, 73)
    assert run2.lookback("linkedin") == (True, 168)        # still owed its full first-run window
    # a company added later shows all its open roles once, however old
    old = dict(LEVER[0], id="lv9", text="Integration Architect", hostedUrl="https://jobs.lever.co/newco/lv9",
               createdAt=int((NOW - timedelta(days=40)).timestamp() * 1000))

    def handler2(request):
        if "lever.co/v0/postings/newco" in str(request.url):
            return httpx.Response(200, json=[old])
        return handler(request)

    with open(tmp / "companies.yaml", "a") as f:
        f.write("  - {name: NewCo, ats: lever, token: newco}\n")
    run3 = pl.Run(dry_run=True, client=httpx.Client(transport=httpx.MockTransport(handler2)), scorer=FakeScorer(), now=later)
    run3.execute()
    assert run3.store.rows("company='NewCo'")[0]["status"] == "scored"
    # ...but only once: the same 40-day-old role under a new title is too old on the next run
    old2 = dict(old, id="lv10", text="Middleware Architect", hostedUrl="https://jobs.lever.co/newco/lv10")

    def handler3(request):
        if "lever.co/v0/postings/newco" in str(request.url):
            return httpx.Response(200, json=[old, old2])
        return handler(request)

    run4 = pl.Run(dry_run=True, client=httpx.Client(transport=httpx.MockTransport(handler3)), scorer=FakeScorer(),
                  now=later + timedelta(days=1))
    run4.execute()
    assert "older than" in run4.store.rows("title='Middleware Architect' AND company='NewCo'")[0]["filter_reason"]


def test_feedback_matches_links_with_tracking_parameters(home):
    tmp, pl = home
    f = pl.Run._url_id
    assert f("https://www.linkedin.com/jobs/view/integration-architect-at-acme-4012345678?refId=abc&trk=x") == "linkedin:4012345678"
    assert f("https://www.linkedin.com/jobs/view/4012345678/") == "linkedin:4012345678"
    assert f("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=x") == "linkedin:4012345678"
    assert f("https://www.dice.com/job-detail/d1?utm_source=mail") == f("http://dice.com/job-detail/d1/")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pl.Run(dry_run=True, client=client, scorer=FakeScorer(), now=NOW).execute()
    (tmp / "feedback.yaml").write_text(
        "applied:\n  - https://www.dice.com/job-detail/d1?utm_source=email&utm_medium=digest\n"
        "not_interested: []\nblocked_companies: [Garden State Bank]\n")
    run2 = pl.Run(dry_run=True, client=client, scorer=FakeScorer(), now=NOW + timedelta(hours=1))
    run2.execute()
    assert run2.store.rows("company='Apex Systems'")[0]["status"] == "applied"
    assert run2.store.rows("company='Garden State Bank'")[0]["filter_reason"] == "company blocked in feedback.yaml"


def test_location_and_work_model_regressions():
    from jobradar.textparse import detect_work_model, parse_location
    assert parse_location("New York, NY") == ("New York", "NY", "US")
    assert parse_location("New York, New York, United States") == ("New York", "NY", "US")
    assert parse_location("New York, NY (Hybrid)")[:2] == ("New York", "NY")
    assert parse_location("Washington, DC") == ("Washington", "DC", "US")
    assert parse_location("New London, CT")[1:] == ("CT", "US")
    assert parse_location("Albuquerque, New Mexico")[1:] == ("NM", "US")
    assert parse_location("London, United Kingdom")[2] == "NON_US"
    assert parse_location("New York, United States") == (None, "NY", "US")       # the state: town unknown
    tech = "Design our hybrid cloud integration platform and the hybrid integration strategy across on-prem and SaaS."
    assert detect_work_model("Integration Architect", "Austin, TX", tech, True, "remote") == "remote"
    assert detect_work_model("Integration Architect", "Austin, TX", tech + " This role is hybrid, 3 days in office.", True, "remote") == "hybrid"
    assert detect_work_model("Architect", "Jersey City, NJ", "Hybrid. You will design payment integrations.", None, None) == "hybrid"
    assert detect_work_model("Architect", "Dallas, TX", "Work model: Hybrid", True, None) == "hybrid"


def test_filter_regressions():
    import yaml
    from jobradar.collectors.linkedin import to_job
    from jobradar.filters import apply_filters, required_blockers
    from jobradar.textparse import parse_salary_text
    cfg = yaml.safe_load((FIX / "config.yaml").read_text())
    core = cfg["skills_gate"]["core"]
    block = cfg["skills_gate"]["drop_if_required"]
    assert required_blockers("Experience with an iPaaS such as MuleSoft, Boomi or Workato is required.", block, core) == []
    assert required_blockers("Boomi experience is required.", block, core) == ["Boomi"]
    assert required_blockers("Active Secret clearance is required. MuleSoft a plus.", block, core) == ["secret clearance"]
    assert parse_salary_text("Pay: $105,000 - $135,000 in most locations; $150,000 - $195,000 in NY and NJ.") == (150000, 195000)
    assert parse_salary_text("Sign-on bonus of $30,000 to $40,000. Base salary $170,000 to $210,000.") == (170000, 210000)

    body = "We need an integration architect with MuleSoft, IBM MQ, Kafka, REST API, AWS and microservices experience. " * 3

    def li(**kw):
        it = {"title": "Integration Architect", "companyName": "Acme", "link": "https://www.linkedin.com/jobs/view/1",
              "descriptionText": body, "postedAtTimestamp": NOW.timestamp() * 1000, "employmentType": "Full-time"}
        return apply_filters(to_job(it | kw), cfg, {}, NOW)

    assert li(location="New York, NY", workplaceTypes=["On-site"]).reason == "Onsite, New York City"
    assert li(location="New York, NY", workplaceTypes=["Hybrid"]).status == "candidate"
    ny_state = li(location="New York, United States", workplaceTypes=["Hybrid"])
    assert (ny_state.status, ny_state.location_unclear) == ("candidate", True)       # Claude decides
    assert li(location="Albany, NY", workplaceTypes=["Hybrid"]).status == "filtered"
    assert li(location="Austin, TX", workplaceTypes=["Remote"], descriptionText=body + " Own the hybrid cloud roadmap.").status == "candidate"
    low_pay = li(location="United States", workplaceTypes=["Remote"], descriptionText=body + " Salary $95,000 - $110,000.")
    assert (low_pay.status, low_pay.salary_note) == ("candidate", "$95,000 to $110,000")          # shown, not filtered
    contract = li(location="United States", workplaceTypes=["Remote"], employmentType="Contract",
                  descriptionText=body + " 6 month contract, C2C or W2, $90/hr.")
    assert contract.status == "candidate"
    assert to_job({"title": "x", "link": "y", "employmentType": "Contract", "descriptionText": "W2 only, no C2C."}).employment_type == "Contract (W2)"
    assert to_job({"title": "x", "link": "y", "employmentType": "Contract", "descriptionText": "Corp-to-corp welcome."}).employment_type == "Contract (C2C)"
    assert li(location="United States", workplaceTypes=["Remote"], employmentType="Part-time").status == "filtered"


def test_scorer_rules_follow_the_pay_setting():
    from jobradar.scorer import _system
    import yaml
    cfg = yaml.safe_load((FIX / "config.yaml").read_text())
    assert "Pay is not a criterion" in _system("profile", [], None, cfg)[0]["text"]
    assert "USD 120,000" in _system("profile", [], 120000, cfg)[0]["text"]
    rules = _system("profile", [], None, cfg)[0]["text"]
    assert "within 25 miles of New York City" in rules and "anywhere in Delaware" in rules     # search area comes from config
    assert "unless they require living outside NJ, NY, PA, DE" in rules


def test_shipped_example_config_and_profile_work(tmp_path, monkeypatch):
    """The example config.yaml and profile in the repo root must load and run end to end."""
    import importlib
    import yaml
    cfg = yaml.safe_load((REPO / "config.yaml").read_text())
    for pattern in cfg["titles"]["include"] + cfg["titles"]["exclude"]:
        re.compile(pattern)
    assert cfg["alerts"]["to"].endswith("@example.com")            # no real address ships in the repo
    for name in ["config.yaml", "feedback.yaml", "companies.yaml"]:
        shutil.copy(REPO / name, tmp_path / name)
    (tmp_path / "config.yaml").write_text((tmp_path / "config.yaml").read_text().replace("pause_seconds: 1 ", "pause_seconds: 0 "))
    (tmp_path / "profile").mkdir()
    shutil.copy(REPO / "profile" / "profile.md", tmp_path / "profile" / "profile.md")
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    monkeypatch.setenv("JSEARCH_API_KEY", "test")
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("GMAIL_USER", raising=False)
    import jobradar.pipeline as pl
    importlib.reload(pl)
    posting = js(1, "Senior Data Engineer", "Lakeshore Retail", "Naperville", "IL",
                 desc="Hybrid, 2 days per week in the office. Build Spark and Airflow data pipelines in Python and SQL "
                      "feeding a Snowflake data warehouse. dbt experience is a plus.")
    far = js(2, "Senior Data Engineer", "Coastal Co", "Miami", "FL", desc=posting["job_description"])

    def example_handler(request):
        if "jsearch" in str(request.url):
            first = request.url.params.get("query", "").startswith("senior data engineer in Chicago")
            return httpx.Response(200, json={"data": [posting, far] if first else []})
        return handler(request)

    run = pl.Run(dry_run=True, client=httpx.Client(transport=httpx.MockTransport(example_handler)), scorer=FakeScorer(), now=NOW)
    counts = run.execute()
    assert counts["collected"] == 2
    kept = run.store.rows("company='Lakeshore Retail'")[0]
    assert (kept["status"], kept["location_label"]) == ("scored", "Hybrid, Chicago")         # town list in the config
    assert "Spark" in kept["skills"] and "Airflow" in kept["skills"]
    assert "outside the search area" in run.store.rows("company='Coastal Co'")[0]["filter_reason"]
    assert (tmp_path / "out" / "emails" / "digest.html").exists()
    assert "within 30 miles of Chicago" in __import__("jobradar.scorer", fromlist=["x"]).location_rule(run.cfg)


class FakeBatchScorer(FakeScorer):
    """Speaks the batch interface of the real Scorer. A batch ends after `checks_needed` status checks."""

    def __init__(self, checks_needed=1, fail_keys=()):
        super().__init__()
        self.checks_needed, self.fail_keys = checks_needed, set(fail_keys)
        self.batches: dict[str, dict] = {}
        self.direct_calls = 0

    def triage(self, row):
        self.direct_calls += 1
        return super().triage(row)

    def assess(self, row):
        self.direct_calls += 1
        return super().assess(row)

    def batch_submit(self, kind, rows):
        bid = f"msgbatch_{kind}_{len(self.batches)}"
        fn = FakeScorer.triage if kind == "triage" else FakeScorer.assess
        self.batches[bid] = {"kind": kind, "checks": 0,
                             "results": {r["key"]: (("error", "batch request errored") if r["title"] in self.fail_keys
                                                    else ("ok", fn(self, r))) for r in rows}}
        return bid

    def batch_ended(self, bid):
        self.batches[bid]["checks"] += 1
        return self.batches[bid]["checks"] >= self.checks_needed

    def batch_results(self, bid):
        return self.batches[bid]["results"]


def _default_config(tmp):
    """The config as shipped (24-hour window, no limits, batch on), with only the Dice pause removed."""
    (tmp / "config.yaml").write_text((FIX / "config.yaml").read_text().replace("pause_seconds: 1 ", "pause_seconds: 0 "))


def test_batch_scoring_completes_in_one_run_when_claude_is_quick(home):
    tmp, pl = home
    _default_config(tmp)
    scorer = FakeBatchScorer(checks_needed=1, fail_keys={"Agentic AI Architect"})
    slept = []
    run = pl.Run(dry_run=True, client=httpx.Client(transport=httpx.MockTransport(handler)), scorer=scorer, now=NOW,
                 final=False, sleep=slept.append)
    c = run.execute()
    kinds = [b["kind"] for b in scorer.batches.values()]
    assert kinds == ["triage", "deep"]                              # one batch per pass, in order
    assert c["batches_pending"] == 0 and slept == []
    assert c["triaged"] >= 8 and c["assessed"] >= 6
    # direct calls: one probe per model on first use (2), plus the one failed request retried in each pass (2)
    assert scorer.direct_calls == 4
    assert json.loads(run.store.get_meta("claude_models"))["checked"] == sorted(
        {run.cfg["scoring"]["triage_model"], run.cfg["scoring"]["deep_model"]})
    assert run.store.rows("title='Agentic AI Architect'")[0]["status"] == "scored"
    assert run.store.rows("status IN ('candidate','awaiting_deep')") == []      # no per-run limit: nothing left waiting
    assert (tmp / "out" / "emails" / "digest.html").exists()        # scoring complete, so the digest went out
    assert run.store.get_meta("digest_due") == "0"


def test_batch_scoring_carries_over_runs_and_reads_sources_once(home):
    tmp, pl = home
    _default_config(tmp)
    scorer = FakeBatchScorer(checks_needed=3)       # each batch needs three status checks before it ends
    client = httpx.Client(transport=httpx.MockTransport(handler))
    digest = tmp / "out" / "emails" / "digest.html"

    def run_at(minutes, final=False):
        r = pl.Run(dry_run=True, client=client, scorer=scorer, now=NOW + timedelta(minutes=minutes),
                   final=final, wait_minutes=0, sleep=lambda s: None)
        return r, r.execute()

    r1, c1 = run_at(0)                              # 6:23 run: reads sources, submits the quick pass, does not wait
    assert c1["collected"] > 0 and c1["batches_pending"] == 1 and c1["triaged"] == 1     # 1 = the one-time model probe
    assert not digest.exists()                      # held: Claude is still scoring
    calls_after_first = len(DICE_CALLS)

    r2, c2 = run_at(30)                             # still processing
    assert c2["collected"] == 0 and len(DICE_CALLS) == calls_after_first        # sources are not read again
    assert c2["batches_pending"] == 1 and not digest.exists()

    r3, c3 = run_at(75)                             # quick pass done: collected, full assessments submitted
    assert c3["triaged"] >= 8 and c3["batches_pending"] == 1
    assert [b["kind"] for b in scorer.batches.values()] == ["triage", "deep"]
    assert not digest.exists()

    r4, c4 = run_at(135, final=True)                # last call of the morning: batch still out, so email what is ready
    assert c4["batches_pending"] == 1 and digest.exists()
    assert "still being scored by Claude" in digest.read_text()
    digest.unlink()

    r5, c5 = run_at(140)                            # the batch ends; results are applied, held for the next digest
    assert c5["assessed"] >= 6 and c5["batches_pending"] == 0
    assert r5.store.rows("status='scored' AND digested_at IS NULL") != []
    assert scorer.direct_calls == 2                 # only the one-time probe per model; everything else went through batches

    r6, c6 = run_at(24 * 60)                        # next morning: sources read again, yesterday's results in this digest
    assert c6["collected"] > 0 and digest.exists()
    assert r6.store.rows("status='scored' AND digested_at IS NULL") == []


def test_default_window_is_one_day_on_every_source_every_time(home):
    tmp, pl = home
    _default_config(tmp)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    run = pl.Run(dry_run=True, client=client, scorer=FakeScorer(), now=NOW)
    for source in ("jsearch", "linkedin", "dice", "ats"):
        assert run.lookback(source) == (False, 26)          # 24 hours plus the 2-hour overlap, even on a first run
    run.execute()
    assert DICE_CALLS[0]["posted_date"] == "ONE"
    late = pl.Run(dry_run=True, client=client, scorer=FakeScorer(), now=NOW + timedelta(days=5))
    assert late.lookback("linkedin") == (False, 26)         # no catch-up after days without a run
    from jobradar.collectors.linkedin import search_urls
    assert "f_TPR=r93600" in search_urls(run.cfg, 26)[0]
    from jobradar.collectors.jsearch import date_posted_for
    assert date_posted_for(26) == "3days"
    # a posting older than the window is dropped, and a company added later gets no backfill
    assert "older than 26 hours" in run.store.rows("company='Old Post Co'")[0]["filter_reason"]


def test_model_that_refuses_forced_tool_choice_is_remembered_between_runs(home):
    """A model that rejects a forced tool_choice is found by the one-time probe, the batch is then built
    without forcing, and the next run already knows (no failed batch, no repeat probe)."""
    tmp, pl = home
    _default_config(tmp)

    class Picky(FakeBatchScorer):
        def __init__(self, deep_model):
            super().__init__(checks_needed=1)
            self._auto_choice: set[str] = set()
            self.deep_model, self.forced_in_batches = deep_model, []

        def assess(self, row):
            self._auto_choice.add(self.deep_model)          # what the real Scorer learns from the HTTP 400
            return super().assess(row)

        def batch_submit(self, kind, rows):
            if kind == "deep":
                self.forced_in_batches.append(self.deep_model not in self._auto_choice)
            return super().batch_submit(kind, rows)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = pl.Run(dry_run=True, client=client, scorer=None, now=NOW, final=True, sleep=lambda s: None)
    first.scorer = Picky(first.cfg["scoring"]["deep_model"])
    first.execute()
    assert first.scorer.forced_in_batches == [False]         # the deep batch was not forced
    saved = json.loads(first.store.get_meta("claude_models"))
    assert saved["auto_choice"] == [first.cfg["scoring"]["deep_model"]]

    second = pl.Run(dry_run=True, client=client, scorer=None, now=NOW + timedelta(days=1), final=True, sleep=lambda s: None)
    second.scorer = Picky(second.cfg["scoring"]["deep_model"])
    second.score([])
    assert second.cfg["scoring"]["deep_model"] in second.scorer._auto_choice     # loaded from the last run
    assert second.scorer.direct_calls == 0                                       # no repeat probe


def test_real_scorer_speaks_the_batch_api(tmp_path):
    """The real Scorer against the real Anthropic SDK, with the HTTP layer replaced: checks the batch
    request that would be sent and that results in every state are read back correctly."""
    import anthropic
    import yaml
    from jobradar.scorer import Scorer, clean_triage
    try:                        # newer Anthropic SDKs ship their own HTTP library; older ones use httpx
        import httpx2 as hx
    except ImportError:
        hx = httpx
    seen = {}

    def api(request):
        path = request.url.path
        if request.method == "POST" and path.endswith("/messages/batches"):
            seen["body"] = json.loads(request.content)
            return hx.Response(200, json=batch("in_progress"))
        if path.endswith("/results"):
            ok = {"id": "msg_1", "type": "message", "role": "assistant", "model": "m", "stop_reason": "tool_use",
                  "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 20},
                  "content": [{"type": "tool_use", "id": "tu_1", "name": "record_triage",
                               "input": {"score": 8, "reason": "fits", "location_check": "remote_us", "pay_below_floor": False}}]}
            lines = [{"custom_id": "aaaa1111bbbb2222", "result": {"type": "succeeded", "message": ok}},
                     {"custom_id": "cccc3333dddd4444", "result": {"type": "errored", "error": {
                         "type": "error", "error": {"type": "invalid_request_error", "message": "bad tool_choice"}}}},
                     {"custom_id": "eeee5555ffff6666", "result": {"type": "expired"}}]
            return hx.Response(200, text="\n".join(json.dumps(x) for x in lines),
                                  headers={"content-type": "application/x-jsonl"})
        return hx.Response(200, json=batch("ended"))

    def batch(status):
        return {"id": "msgbatch_1", "type": "message_batch", "processing_status": status,
                "request_counts": {"processing": 0, "succeeded": 1, "errored": 1, "canceled": 0, "expired": 1},
                "created_at": "2026-11-20T12:00:00Z", "expires_at": "2026-11-21T12:00:00Z", "ended_at": None,
                "archived_at": None, "cancel_initiated_at": None,
                "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"}

    client = anthropic.Anthropic(api_key="test", http_client=hx.Client(transport=hx.MockTransport(api)))
    cfg = yaml.safe_load((FIX / "config.yaml").read_text())
    (tmp_path / "profile.md").write_text("profile text")
    scorer = Scorer(cfg, tmp_path / "profile.md", [], client=client)
    row = {"key": "aaaa1111bbbb2222", "title": "Integration Architect", "company": "Acme", "location": "Remote",
           "work_model": "remote", "salary_note": "", "publisher": "Dice", "source": "dice", "description": "MuleSoft",
           "employment_type": "Contract (C2C)"}
    assert scorer.batch_submit("triage", [row]) == "msgbatch_1"
    req = seen["body"]["requests"][0]
    assert req["custom_id"] == "aaaa1111bbbb2222"
    params = req["params"]
    assert params["model"] == cfg["scoring"]["triage_model"]
    assert params["tool_choice"] == {"type": "tool", "name": "record_triage"}
    assert params["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}     # profile cached for an hour
    assert "Employment type: Contract (C2C)" in params["messages"][0]["content"]
    assert scorer.batch_ended("msgbatch_1") is True
    results = scorer.batch_results("msgbatch_1")
    assert results["aaaa1111bbbb2222"][0] == "ok" and clean_triage(results["aaaa1111bbbb2222"][1])["score"] == 8
    assert results["cccc3333dddd4444"] == ("error", "batch request errored: bad tool_choice")
    assert results["eeee5555ffff6666"] == ("error", "batch request expired")


def test_parsers():
    from jobradar.textparse import detect_work_model, parse_location, parse_salary_text, residency_states
    assert parse_location("Jersey City, NJ") == ("Jersey City", "NJ", "US")
    assert parse_location("Philadelphia, Pennsylvania, United States")[1] == "PA"
    assert parse_location("Toronto, ON, Canada")[2] == "NON_US"
    assert parse_salary_text("The range is $140K-$165K annually") == (140000, 165000)
    assert parse_salary_text("$70 - $80 per hour") == (145600, 166400)
    assert parse_salary_text("Base pay $150,000 to $180,000") == (150000, 180000)
    assert residency_states("Applicants must reside in New Jersey, New York or Pennsylvania.") == {"NJ", "NY", "PA"}
    assert residency_states("You must be located in the United States.") is None
    assert detect_work_model("Architect", "Remote", "Work 2 days a week in the office in Dallas.", True, None) == "hybrid"
    assert detect_work_model("Architect", "Newark, NJ", "Fully remote role.", None, None) == "remote"


def test_skill_terms():
    from jobradar.filters import required_blockers, skills_in
    terms = ["event-driven", "REST API", "MQ | IBM MQ | WebSphere MQ", "MCP | Model Context Protocol", "pub/sub"]
    assert skills_in("Event driven design with REST APIs over WebSphere MQ and pub/sub", terms) == \
        ["event-driven", "REST API", "MQ", "pub/sub"]
    assert skills_in("Model Context Protocol servers", terms) == ["MCP"]
    assert skills_in("in the workplace we squeeze", ["MQ", "EAI"]) == []
    assert required_blockers("Dell Boomi is a nice to have.", ["Boomi"]) == []
    assert required_blockers("Must have 5+ years of Boomi.", ["Boomi"]) == ["Boomi"]


def test_skill_names_and_aliases_in_gate():
    import yaml
    from jobradar.filters import skills_in
    g = yaml.safe_load((FIX / "config.yaml").read_text())["skills_gate"]
    camel = "Build routes in Red Hat Fuse and Apache Camel on Spring Boot with ActiveMQ, X12 EDI partner feeds"
    assert {"Apache Camel", "Red Hat Fuse", "ActiveMQ", "X12"} <= set(skills_in(camel, g["core"]))
    ms = "Pipelines in Azure Data Factory and SSIS landing into Synapse and Microsoft Fabric, alerts in Splunk"
    assert {"SSIS", "Azure Data Factory"} <= set(skills_in(ms, g["core"]))
    assert {"Synapse", "Microsoft Fabric", "Splunk"} <= set(skills_in(ms, g["supporting"]))
    assert "Microsoft Fabric" not in skills_in("Kids apparel with soft cotton fabric", g["supporting"])
    fde = "As a Forward Deployed Engineer you ship LLM agents with function calling into customer systems on the Anypoint Platform"
    assert {"forward deployed", "LLM", "tool calling", "MuleSoft"} <= set(skills_in(fde, g["core"]))


def test_title_filter():
    import yaml
    from jobradar.filters import title_ok
    cfg = yaml.safe_load((FIX / "config.yaml").read_text())
    keep = ["Associate Director, Integration Architecture", "Associate Vice President, Enterprise Architecture",
            "Associate VP Engineering", "AVP, Data Transformation & Integration Manager", "Senior Software Engineer",
            "Sr. Software Engineer, Payments", "Engineering Manager, Platform", "Manager, Software Engineering",
            "Application Architect", "Platform Engineer", "Senior Cloud Engineer", "Integration Developer",
            "Integration Analyst", "Integration Specialist", "Salesforce Integration Architect"]
    keep += ["Application Architect", "Platform Engineer", "Senior Integration Engineer", "Senior Integration Manager",
             "Forward Deployed Engineer", "Forward-Deployed AI Engineer", "AI Engineer", "Gen AI Engineer", "GenAI Engineer",
             "AI/Gen AI Engineer", "Software Engineer, AI", "Agentic AI Developer", "Integration Engineer", "Integration Lead",
             "Middleware Architect", "Integration Architect", "Solutions Architect", "Enterprise Applications Lead",
             "Integration Developer", "Senior Integration Developer", "MuleSoft Integration Developer",
             "Director, Enterprise Applications", "Senior Manager, API Platform"]
    drop = ["Associate Integration Developer", "Associate Software Engineer", "Junior Platform Engineer",
            "Sales Engineer", "Landscape Architect", "Software Engineer Intern"]
    for t in keep:
        assert title_ok(t, cfg)[0], t
    for t in drop:
        assert not title_ok(t, cfg)[0], t


def test_add_company_url_parsing():
    from jobradar.cli import parse_careers_url
    assert parse_careers_url("https://job-boards.greenhouse.io/datadog")["token"] == "datadog"
    assert parse_careers_url("https://jobs.lever.co/palantir/abc")["ats"] == "lever"
    wd = parse_careers_url("https://comcast.wd5.myworkdayjobs.com/en-US/Comcast_Careers?q=architect")
    assert wd == {"ats": "workday", "host": "comcast.wd5.myworkdayjobs.com", "tenant": "comcast", "site": "Comcast_Careers"}
