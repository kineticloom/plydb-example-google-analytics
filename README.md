# Google Analytics AI Starter

Accompanying [blog post](https://www.plydb.com/blog/plydb-example-google-analytics/).

---

Chat with your Google Analytics 4 Data.

[PlyDB](https://www.plydb.com/) — the universal database gateway for AI agents —
brings conversational analytics to your Google Analytics 4 (GA4) data without a
warehouse, a pipeline, or a line of SQL. This repo downloads your GA4 data
locally using the
[google-analytics-data](https://github.com/googleapis/google-cloud-python/tree/main/packages/google-analytics-data)
Python library, then gives your AI agent SQL access via MCP or CLI — ask
questions in plain English, get answers from your real data.

> Which pages have the highest bounce rate — and which traffic sources bring
> those visitors?

> How has organic search traffic trended week-over-week over the past year?

> Which countries send the most users, and how do their session engagement
> metrics compare?

> What events are most frequently triggered, and what share of users fire each
> one?

## Who this is for

Growth teams, product managers, and marketing analysts who want AI-powered
Google Analytics analysis without standing up a data warehouse. Use it as-is or
adapt it as the GA4 layer in a broader multi-source analytics setup.

## How it works

- **Download**: The Google Analytics Data API pulls your GA4 data into local
  Parquet files — your data never leaves your machine. Data is as fresh as your
  last download; run it on a schedule (cron, GitHub Actions, Airflow) to keep it
  current.
- **Query**: PlyDB gives your AI agent SQL access to those files with zero ETL
  and no pipelines, via MCP or CLI. Works with Claude, ChatGPT, Gemini, and any
  MCP- or CLI-compatible agent.
- **Understand**: A semantic overlay teaches the agent your GA4 data model so it
  writes more accurate queries — and compounds in accuracy over time as you use
  it.

---

## Workflow

1. [Install prerequisites](#step-1--install-prerequisites)
2. [Download Google Analytics data](#step-2--download-google-analytics-data)
3. [Configure PlyDB](#step-3--configure-plydb)
4. [Start analyzing](#step-4--start-analyzing)

---

## Step 1 — Install prerequisites

### PlyDB

PlyDB is the universal database gateway for AI agents — it gives your agent SQL
access to local and remote data sources with zero ETL and no pipelines, via MCP
or CLI. Your agent translates your questions into SQL; PlyDB executes them
against your data wherever it lives. Works with Claude, ChatGPT, Gemini, and any
MCP- or CLI-compatible agent.

**New to PlyDB?** The [PlyDB quickstart](https://www.plydb.com/docs/quickstart/)
walks through installation, config, and your first queries end-to-end.

### Python environment

The data download script requires Python 3.8+. Create a virtual environment and
install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Google credentials

The Google Analytics Data API is free — no billing setup or payment method
required. You do need a Google Cloud project to create credentials, but the
project itself can remain on the free tier.

The download script authenticates using a Google Cloud service account. If you
already have Application Default Credentials configured via the `gcloud` CLI,
you can skip to [Option B](#option-b--application-default-credentials).

**Option A — Service account JSON (recommended for scripts)**

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and select
   or create a project.
2. Navigate to **APIs & Services → Enabled APIs** and enable the **Google
   Analytics Data API**.
3. Navigate to **IAM & Admin → Service Accounts** and create a new service
   account (any name, no roles needed at the project level).
4. Under the service account, go to **Keys → Add Key → Create new key → JSON**.
   Download the JSON file and save it somewhere safe (e.g.
   `~/.config/ga-key.json`).
5. In your [Google Analytics](https://analytics.google.com/) account, go to
   **Admin → Property → Property Access Management** and add the service account
   email (from the JSON file, `client_email` field) as a **Viewer**.
6. Export the path to your key file:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/ga-key.json"
```

**Option B — Application Default Credentials**

Use this option if you prefer to authenticate as yourself rather than via a
service account.

1. Make sure the Google account you'll use has **Viewer** access to the GA4
   property (**Google Analytics → Admin → Property → Property Access
   Management**).
2. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install).
3. Open the [Google Cloud Console](https://console.cloud.google.com/) and select
   or create a project.
4. Navigate to **APIs & Services → Enabled APIs** and enable the **Google
   Analytics Data API**.
5. Navigate to **APIs & Services → Credentials → Create Credentials → OAuth
   client ID**. Select **Desktop app** as the application type and give it a
   name.
6. Download the OAuth client ID JSON file.
7. Run the following, passing the downloaded file and the required Analytics
   scope:

```bash
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/cloud-platform \
  --client-id-file=/path/to/oauth/clientid/file.json
```

No environment variable needed — the library picks up ADC automatically.

---

## Step 2 — Download Google Analytics data

### Set your GA4 property ID

Your GA4 property ID is the numeric ID shown in Google Analytics under **Admin →
Property → Property details**. It looks like `123456789` (digits only, not
prefixed with `properties/`).

```bash
export GA4_PROPERTY_ID="123456789"
```

### Run the download script

```bash
python download_ga_data.py
```

By default this fetches the last 365 days of data. Options:

```bash
# Custom date range
python download_ga_data.py --start-date 2024-01-01 --end-date 2024-12-31

# Last N days
python download_ga_data.py --days 90
```

The script downloads four GA4 reports and saves them as Parquet files under
`data/google-analytics/`, partitioned by date.

| Report            | Dimensions                                   | Metrics                                                        |
| ----------------- | -------------------------------------------- | -------------------------------------------------------------- |
| `traffic_sources` | date, landing page, source, medium, campaign | sessions, users, new users, bounce rate, avg session duration  |
| `page_views`      | date, page path, page title                  | page views, sessions, avg session duration, bounce rate, users |
| `events`          | date, event name                             | event count, users, events per user                            |
| `user_segments`   | date, country, device, browser, OS           | sessions, users, new users                                     |

Each date is its own partition, so re-running the script for a date range only
overwrites the affected partitions — historical data outside that range is
untouched. This makes incremental daily runs safe and efficient.

### Filter the data

Use `--filter` to apply dimension filters server-side before download. Filters
are applied across all reports. Use `=` to include and `!=` to exclude. Multiple
filters are AND-ed.

```bash
# Exclude a known spam source
python download_ga_data.py --filter 'sessionSource!=spam.com'

# Only download data for a specific country
python download_ga_data.py --filter 'country=United States'

# Combine filters
python download_ga_data.py --filter 'country=United States' --filter 'deviceCategory!=tablet'
```

---

## Step 3 — Configure PlyDB

`plydb-config.json` is pre-configured to point PlyDB at the Parquet files in
`data/google-analytics/`, with `plydb-overlay.yaml` providing semantic context
about the GA4 data model. No changes needed — open your agent in this directory
and it will pick up the config automatically.

---

## Step 4 — Start analyzing

Open Claude Code (or any PlyDB-compatible agent) in this directory and start
asking questions. The agent translates your questions into SQL, runs them
against your local GA4 data via PlyDB, and returns results — all without the
data leaving your machine.

### Sample prompts

**Top pages by engagement:** Ask which pages have the most sessions but the
lowest bounce rate. The agent will rank pages by sessions, show bounce rate and
average session duration for each, and surface the ones where visitors are
genuinely engaged versus just landing and leaving.

**Traffic source quality:** Ask the agent to compare bounce rate and average
session duration across all source/medium combinations. It will show you which
channels bring volume versus which bring quality — often very different answers.

**Organic search trends:** Ask for weekly organic search sessions over the past
year. The agent will run the query, describe the trend, and flag any weeks with
an unusual spike or drop worth investigating.

**Geographic breakdown:** Ask which countries generate the most sessions and how
engagement metrics compare across the top 10. Useful for identifying markets
that are underserved relative to their traffic share.

**Weekly briefing:** Ask the agent what changed most in the last 7 days compared
to the prior 7 days — across sessions, bounce rate, and top pages. Instead of
opening a dashboard and hunting for anomalies yourself, you get a plain-English
summary of what moved, what's worth investigating, and what looks like noise.

**Content gap diagnosis:** Ask the agent to identify pages with high traffic but
above-average bounce rates, then hypothesize why visitors are leaving — intent
mismatch, missing CTAs, thin content — and recommend which pages are worth
redesigning versus cutting. This is the kind of multi-step reasoning that takes
an analyst an afternoon and an AI agent a few seconds.

**Distribution channel strategy:** Ask which referral sources send
disproportionately high-quality traffic relative to their session volume. The
agent will compute a quality score from the available engagement signals —
bounce rate (did the visitor stay?) and average session duration (did they
engage?) — rank sources by quality against volume, identify which communities or
partners are punching above their weight, and suggest where to double down on
distribution before competitors notice the same signal.

---

## Going further

This repo is intentionally minimal — a working starting point, not a production
system. Here are a few natural directions to take it:

**Revenue attribution:** Join GA4 traffic source data with revenue data from
[Stripe](https://github.com/plydb/plydb-example-stripe) to answer questions like
"which acquisition channel produces the highest LTV customers?" — a query that
spans two completely separate data sources in one SQL statement.

**Ad spend and CAC:** Add Google Ads or Facebook Ads data alongside GA4 to
calculate cost-per-session and cost-per-conversion by campaign, without moving
any data to a warehouse.

**Scheduled loads:** Run the download script on a schedule (cron, Airflow,
GitHub Actions) to keep GA4 data fresh. PlyDB queries the updated files without
any pipeline changes.

**Proactive monitoring instead of static dashboards:** Schedule your agent to
run nightly and surface what changed: a traffic spike, a page with a sudden
bounce rate increase, a campaign outperforming expectations. Instead of checking
a dashboard, you get a briefing with anomalies already flagged and context
attached. Agents that support scheduled runs (such as
[Claude Code](https://claude.ai/code)) can automate this end-to-end.

**Richer semantic context:** The `plydb-overlay.yaml` file is a starting point.
After any analysis session, ask your agent to update it with what it learned —
your site's key pages, event naming conventions, campaign taxonomy. These
compound over time and make future sessions more accurate.

---

## Data source

| Source                                                                                                 | Description                                                                      |
| ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| [Google Analytics Data API (GA4)](https://developers.google.com/analytics/devguides/reporting/data/v1) | Sessions, users, page views, events, traffic sources, geography, and device data |

---

## FAQ

**Do I need to know SQL?** No — your AI agent translates your questions into SQL
and runs them automatically. You just ask in plain English.

**Does my data leave my machine?** No. The Google Analytics Data API downloads
your GA4 data to local Parquet files. PlyDB queries those files on your machine —
nothing is sent to an external service.

**Which AI agents does this work with?** Any agent that supports MCP or CLI
tools — including Claude Code, Claude Desktop, ChatGPT, Gemini, Codex, and more.

**Is the data real-time?** Data is as fresh as your last download run. Run the
download script on a schedule (cron, GitHub Actions, Airflow) to keep it current
— PlyDB queries the updated files without any pipeline changes.

**Is PlyDB free?** Yes — PlyDB is
[open source under Apache 2.0](https://github.com/kineticloom/plydb).

---

## Reference

- [Google Analytics Data API quickstart](https://developers.google.com/analytics/devguides/reporting/data/v1/quickstart?account_type=user#python_1)
  — official Python quickstart
- [google-analytics-data Python library](https://github.com/googleapis/google-cloud-python/tree/main/packages/google-analytics-data)
  — source and API reference
- [GA4 dimensions and metrics reference](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema)
  — full list of available dimensions and metrics
- [PlyDB documentation](https://www.plydb.com/docs/) — full PlyDB reference
- [gcloud CLI login](https://docs.cloud.google.com/sdk/gcloud/reference/auth/application-default/login)
  — gcloud auth application-default login
