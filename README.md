# Sentinel

**A lightweight uptime monitor with smart Discord and email alerts.**

Sentinel checks your websites and APIs on a schedule, remembers their state, and alerts you only when something actually changes: a site goes down, recovers, or its TLS certificate is about to expire. No database, no server to babysit, no noise.

[![CI](https://github.com/JudeaTablate/sentinel-uptime-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/JudeaTablate/sentinel-uptime-monitor/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Live demo and documentation:** https://sentinel-uptime-monitor-docs.vercel.app/ (the status panel on that page shows real sites being checked by Sentinel).

## Features

- **Concurrent health checks** across any number of sites, with per-site timeouts, retries and accepted status codes.
- **Smart alerting.** A site must fail several checks in a row before a DOWN alert fires. You get one alert per incident, then a recovery message with the total downtime.
- **Discord and email.** Use either channel or both. Email works with any SMTP provider (Gmail by default).
- **Content checks.** Require a keyword in the response body, so a 200 page that says "maintenance" still counts as down.
- **TLS certificate watch.** Get warned before a certificate expires.
- **`status.json` output** with status, latency, uptime percentage and recent response times, ready for any dashboard or status page.
- **Hosted mode.** A ready-made GitHub Actions workflow runs the checks every 5 minutes and publishes live data to your website. No server needed.
- **Private site lists.** Keep the list of monitored sites in a secret instead of the repository.
- **Secrets stay out of code.** Credentials come from environment variables and are never logged.

## Quick start

```bash
git clone https://github.com/JudeaTablate/sentinel-uptime-monitor.git
cd sentinel-uptime-monitor
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env             # then fill in Discord and/or email settings
python monitor.py --test-alert   # confirm every alert channel works
python monitor.py                # start monitoring
```

Edit `config.yaml` to choose what to watch. The sites in it are examples.

## Configuration

### Secrets (environment variables)

| Variable | Required | Description |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | No | Discord webhook for alerts. |
| `SMTP_USER` | For email | Sender login, for example your Gmail address. |
| `SMTP_PASSWORD` | For email | For Gmail, an [App Password](https://myaccount.google.com/apppasswords), not your normal password. |
| `ALERT_EMAIL_TO` | For email | Who receives alerts. Separate several addresses with commas. |
| `SMTP_HOST`, `SMTP_PORT`, `ALERT_EMAIL_FROM` | No | Defaults: `smtp.gmail.com`, `465`, and the value of `SMTP_USER`. Other ports use STARTTLS. |
| `SENTINEL_CONFIG` | No | The whole YAML config as text. If set, it is used instead of `config.yaml`. |
| `SENTINEL_STATE_DIR` | No | Directory for the log, `state.json` and `status.json`. |

If no channel is configured, Sentinel still monitors and logs, but sends nothing. Put values in a local `.env` file (git-ignored) or your host's secret store, never in `config.yaml` or source code.

### Email alerts with Gmail

1. Turn on 2-Step Verification for your Google account.
2. Open https://myaccount.google.com/apppasswords and create an App Password named "Sentinel".
3. Set `SMTP_USER` to your Gmail address, `SMTP_PASSWORD` to the 16-character code, and `ALERT_EMAIL_TO` to where alerts should go.
4. Run `python monitor.py --test-alert`. It reports `Email: sent` or `Email: FAILED`.

### `config.yaml`

```yaml
settings:
  interval: 60            # seconds between check cycles
  timeout: 10             # seconds before a request counts as failed
  retries: 1              # extra attempts before a check counts as failed
  failure_threshold: 2    # consecutive failed checks before a DOWN alert
  workers: 8              # sites checked in parallel
  ssl_warn_days: 14       # warn when a certificate expires within N days

sites:
  - name: My API
    url: https://api.example.com/health
    expected_status: [200, 204]   # accepted status codes (default: 200)
    timeout: 5                    # per-site override
    keyword: '"status":"ok"'      # response must contain this text
```

### Command line

| Flag | What it does |
| --- | --- |
| `-c, --config PATH` | Use a different config file (default `config.yaml`). |
| `--once` | Run one cycle and exit. Exit code is `1` if any site is failing. |
| `--test-alert` | Send a test alert to every configured channel and exit. |
| `-v, --verbose` | Debug output. |

## How alerting works

```
check -> failed? -> count consecutive failures -> reached threshold? -> DOWN alert (once)
                 \-> succeeded after DOWN -> RECOVERED alert with downtime
```

State lives in `state.json`; the latest summary for dashboards lives in `status.json`.

## Hosted monitoring on GitHub Actions

`.github/workflows/monitor.yml` runs `python monitor.py --once` every 5 minutes, sends alerts, and publishes `status.json` and `state.json` to a `status-data` branch. Your website can fetch live results from:

```
https://raw.githubusercontent.com/<you>/<repo>/status-data/status.json
```

Set these under *Settings → Secrets and variables → Actions*:

| Secret | Value |
| --- | --- |
| `SENTINEL_CONFIG` | Your site list as YAML (same format as `config.yaml`). Keeps your sites out of the public repo. |
| `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` | Email alerts (see above). |
| `DISCORD_WEBHOOK_URL` | Optional Discord alerts. |

Then open the *Actions* tab, choose *Sentinel Uptime Checks*, and click *Run workflow* once to start.

**If the website is on Vercel:** Vercel builds every branch you push, so the workflow's `status-data` branch would trigger a preview deployment every 5 minutes. In the Vercel project, open *Settings → Git → Ignored Build Step*, choose *Custom*, and enter:

```
[ "$VERCEL_GIT_COMMIT_REF" = "main" ] && exit 1 || exit 0
```

This builds `main` only (exit 0 skips a build, exit 1 builds). The workflow also writes a `vercel.json` into the data branch that disables deployments there.

**Good to know:** GitHub may start scheduled runs a few minutes late, and it pauses scheduled workflows in a repository with no activity for 60 days (re-enable them from the *Actions* tab). Free Render services sleep when idle, so give them a longer `timeout`. A check from GitHub's US data centers measures latency from there, not from your users.

## Running it on your own machine or server

```ini
# /etc/systemd/system/sentinel.service
[Unit]
Description=Sentinel uptime monitor
After=network-online.target

[Service]
WorkingDirectory=/opt/sentinel
EnvironmentFile=/opt/sentinel/.env
ExecStart=/opt/sentinel/.venv/bin/python monitor.py
Restart=on-failure
User=sentinel

[Install]
WantedBy=multi-user.target
```

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers config validation, alert state transitions, retries, Discord rate limiting, email delivery, the private config flow, an end-to-end outage-and-recovery run across restarts, and guard tests that fail if a webhook-looking URL or a private project list lands in tracked source.

## Project layout

| Path | Purpose |
| --- | --- |
| `monitor.py` | The monitor: config, checks, state, alerts, CLI |
| `config.yaml` | Example sites and settings (no secrets) |
| `.env.example` | Template for local secrets |
| `tests/` | Test suite |
| `docs/` | Website with the live status panel (deployed on Vercel) |
| `.github/workflows/` | Tests, secret scanning, and the scheduled monitor |

## Security

See [SECURITY.md](SECURITY.md). In short: secrets come from the environment, the webhook URL is validated, no credential is ever logged, and CI scans every push for leaked credentials.

## License

MIT
