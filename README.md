# Job Radar

A personal job search that runs itself on GitHub Actions. Every morning it reads new postings from several job sources, drops the ones that do not fit your rules, has Claude rank the rest against your own profile, and emails you a short list with the reasons.

You describe yourself once in a profile file and your search in a settings file. No server, no database to host: GitHub runs it and keeps its state.

## What you get each morning

One email with the new postings worth your time, best first. Each card shows:

- a rank out of 10, and a verdict: apply, stretch or skip
- company, location, work model, pay if stated, and the employment type (Full-time, Contract (C2C), Contract (W2), Contract to hire)
- hiring-manager fit and keyword match, as percentages
- which of your skills the posting names, the gaps, and the angle to lead with in your resume

Below the cards: postings that fell just under the cut, counts of what was filtered and why, and any source that had a problem.

## How it works

1. **Collect.** New postings from the last 24 hours from:
   - **JSearch** (Google for Jobs: Indeed, Glassdoor, ZipRecruiter, company career pages and part of LinkedIn). Paid API key.
   - **Dice** through Dice's own public MCP server. Free, no key. Strong for contract roles.
   - **Company careers boards** you list yourself (Greenhouse, Lever, Ashby, Workday). Free.
   - **LinkedIn** through a third-party Apify scraper. Optional, off by default, billed by Apify.
2. **Deduplicate.** A job posted on five boards is handled once.
3. **Filter by rules, for free.** Title patterns, location and work model, employment type, an optional pay floor, and a skills check on the description.
4. **Rank with Claude.** A quick pass with a small model scores every survivor; the promising ones get a full assessment with a larger model. Scoring goes through Anthropic's Message Batches API at half price.
5. **Email** the result and save a ranked CSV and HTML report as a run artifact.

## Before you start: keep your copy private

Your copy holds your profile, your search and a database of the postings it has seen. **Create your copy as a private repository.** Nothing in this public repository is personal; yours will be.

Secrets (API keys, your email password) never go in files. They go in GitHub's encrypted secrets, as described below.

## Setup (about 30 minutes)

### 1. Make your own private copy

On this repository's page choose **Use this template**, then **Create a new repository**, and set it to **Private**.

If you prefer the command line:

```bash
git clone https://github.com/<owner>/<this-repo>.git job-radar
cd job-radar
rm -rf .git && git init -b main
git add . && git commit -m "Job Radar"
# create an empty PRIVATE repository on GitHub first, then:
git remote add origin https://github.com/<your-username>/job-radar.git
git push -u origin main
```

### 2. Get the credentials

| Secret name | Needed | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | console.anthropic.com, API Keys. API use is billed separately from a Claude subscription; add a few dollars of credit. |
| `JSEARCH_API_KEY` | Yes, unless you turn JSearch off | openwebninja.com, subscribe to JSearch, copy the key. If you subscribed through RapidAPI instead, set `endpoint: rapidapi` in `config.yaml`. |
| `GMAIL_USER` | Yes | The Gmail address that sends the digest (it can be the same address that receives it). |
| `GMAIL_APP_PASSWORD` | Yes | Google Account, Security, turn on 2-Step Verification, then search "App passwords" and create one. Use that 16-character password, never your normal one. |
| `APIFY_TOKEN` | Only for LinkedIn | apify.com, Settings, API and Integrations, Personal API token. |

### 3. Add them to GitHub

In your repository: **Settings**, **Secrets and variables**, **Actions**, **New repository secret**. Add each one under the exact name above.

### 4. Describe yourself: `profile/profile.md`

This is what Claude ranks against. The file ships with a fictional example; replace it with your own facts and keep the headings:

- **Target roles**: titles, level, employment types, location.
- **Positioning**: your strengths in order.
- **Experience summary**: employers, systems, scale, your role.
- **Confirmed skills**: only what you can defend in an interview.
- **Hard gaps**: what you do not have. This is what keeps poor matches out of your inbox.
- **Calibration** (optional): a few past postings with your own fit estimate.

Be truthful and specific. Claude is told not to assume experience the profile does not show.

### 5. Describe your search: `config.yaml`

The file is a worked example with a comment on every setting. Change at least these:

| Section | What to set |
|---|---|
| `alerts.to` | Your email address |
| `alerts.timezone` | Your time zone |
| `location` | Work models you want, states you could work remote from, and your local areas |
| `titles.include` / `exclude` | Patterns for the job titles you want and do not want |
| `skills_gate.core` / `supporting` | Your skills, and `drop_if_required` for things you lack |
| `sources.jsearch.queries` and `locations` | Your searches |
| `sources.dice.keywords` and `searches` | Your searches |
| `sources.linkedin` | Leave `enabled: false`, or turn it on and set your keyword groups |

A local area is either a whole state or a circle around a point. For a circle, list the towns inside it under `towns`, because many postings carry only "Town, ST" and no coordinates.

Optionally add target companies to `companies.yaml`:

```bash
uv run jobradar add-company "https://boards.greenhouse.io/examplecompany" --check
```

### 6. First run

**Actions**, **job-radar**, **Run workflow**, tick **Also send the daily digest now**, then **Run workflow**.

A manual run reads the sources, waits up to 45 minutes for Claude, and sends the digest. The run page shows a summary of what was collected, filtered and scored. After that it runs by itself every morning.

If GitHub shows the workflows as disabled in a new copy, open the **Actions** tab and enable them.

## The morning schedule

Claude's scoring uses the Message Batches API: the same requests at half the price, answered later, usually well within an hour. Because the answer is not immediate, the workflow starts four times each morning (times are UTC in `.github/workflows/job-radar.yml`; shift them to suit you):

| Run (UTC) | What it does |
|---|---|
| 11:23 | Reads every source for the last 24 hours, filters, submits the quick pass as a batch |
| 11:53 | Collects the quick pass, submits the full assessments |
| 12:38 | Collects the assessments, sends the digest |
| 13:38 | Last call: sends the digest with whatever is ready and says how many postings are still being scored |

Each run waits a few minutes and goes as far as it can, so on a quick day the digest goes out from an earlier run. Only the first run reads the sources, so the follow-up runs cost nothing at the job sources.

To score immediately at full price instead, set `scoring.batch.enabled: false`.

## Everyday use

Everything is done by editing a file in your repository (the GitHub web editor is fine) and committing.

- **Applied, or not interested:** paste the job's link into `feedback.yaml`. Reasons under `not_interested` are shown to Claude so similar roles score lower.
- **Block a company or agency:** add it to `blocked_companies` in `feedback.yaml`.
- **Tune the filters:** the digest shows how many postings each rule dropped. Loosen or tighten `titles` and `skills_gate` in `config.yaml`.
- **Full-time only, or contracts only:** `employment.exclude` in `config.yaml`.
- **A pay floor:** `salary.min_annual_usd` in `config.yaml`.
- **Cap the Claude cost:** set `scoring.max_deep_per_run` to a number.
- **See everything ranked:** open a run in the Actions tab and download the `job-radar-report` artifact (`ranked.csv`, `report.html`).

## Costs

| Item | What to expect |
|---|---|
| GitHub Actions | Free for public repositories. A private repository has 2,000 free minutes a month; four short runs a day use roughly 600 to 1,200. |
| Claude API | Depends on how many postings pass your filters. Batch pricing is half the standard rate. Tight title and skills filters keep it low; `max_triage_per_run` and `max_deep_per_run` cap it. |
| JSearch | Requests a day = queries x locations x pages. Check the current plans on the provider's site. |
| Dice, company boards, Gmail | Free |
| LinkedIn through Apify (optional) | Billed per result by Apify; `max_results_per_run` is a hard cap per run. |

The run summary shows JSearch requests and LinkedIn results used this month.

## Where state is kept

The database of every posting seen (`data/jobs.db`) is saved after each run to a branch named `state` in your repository (a single commit, replaced each time) and restored at the start of the next run. A second copy goes to the GitHub Actions cache. Do not merge or delete the `state` branch. The text of filtered-out postings is cleared after 30 days.

The workflow needs permission to push that branch. It asks for it itself (`contents: write`); if your account forces read-only workflow tokens, allow read and write under Settings, Actions, General, Workflow permissions.

## Running locally

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest                      # offline tests, no network and no keys
cp .env.example .env               # fill in, then: set -a; source .env; set +a
uv run jobradar run --dry-run --direct --force-collect    # real sources, direct Claude calls, email written to out/emails
uv run jobradar calibrate -v       # optional: compare Claude's scores with your own (see calibration/)
```

## Notes on the sources

- **JSearch, Dice and company boards** are read through interfaces their providers publish.
- **LinkedIn** has no public job-search API for individuals. The optional LinkedIn source uses a third-party scraper on Apify that reads LinkedIn's public job pages without a login. Automated collection is against LinkedIn's user agreement. It is off by default; turning it on is your decision.
- This tool only reads postings. It never applies to jobs, messages anyone or logs in to any job site as you.
- Postings are untrusted text. Claude's reply is restricted to a fixed set of fields, and everything shown in the email is escaped.

## Troubleshooting

- **No email:** check the run log for "email not sent" (secrets missing) or an SMTP login error (wrong app password, or 2-Step Verification is off).
- **`model not found`:** update `triage_model` and `deep_model` in `config.yaml` to current Claude model names.
- **HTTP 429 from JSearch:** quota reached. Remove a query or upgrade the plan.
- **"answered but returned no postings at all":** a source ran without error and found nothing, which usually means its searches are too narrow or stopped working. Check that source's section in `config.yaml`.
- **"Apify account is out of credit":** add credit at Apify, or set `sources.linkedin.enabled: false`.
- **Dice problems:** Dice publishes no rate limit. On HTTP 429 the run backs off and then skips Dice for the day; raise `pause_seconds` or trim `keywords`.
- **Everything is filtered:** look at "Why the rest were filtered" in the digest. Usually the title patterns are too narrow, `min_total` is too high, or a circle area has no `towns`.
- **A company board fails:** the run continues and the error is listed in the digest. Re-check the URL with `add-company --check`.
- **Scheduled runs stopped:** GitHub can pause scheduled workflows in a repository with no activity for 60 days. Any commit restarts them.

## Project layout

```
config.yaml                 your search settings (example included)
profile/profile.md          your profile (example included)
companies.yaml              target companies' careers boards
feedback.yaml               applied / not interested / blocked companies
calibration/                optional: past postings with your own fit estimate
src/jobradar/               the code: collectors, filters, scorer, pipeline, email
tests/                      offline tests with their own sample search in tests/fixtures
.github/workflows/          the daily job and the test run
```

## License

MIT. See `LICENSE`.
