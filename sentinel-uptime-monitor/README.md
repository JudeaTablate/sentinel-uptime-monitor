# Sentinel

**A lightweight uptime monitor with smart Discord alerts.**

Sentinel checks your websites and APIs on a schedule, remembers their state, and pings Discord only when something actually changes: a site goes down, recovers, or its TLS certificate is about to expire. No dashboards to host, no database, no noise.

[![CI](https://github.com/JudeaTablate/sentinel-uptime-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/JudeaTablate/sentinel-uptime-monitor/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Live site:** https://judeatablate.github.io/sentinel-uptime-monitor/

## Features

- **Concurrent health checks** across any number of sites, with per-site timeouts, retries and accepted status codes.
- **Smart alerting.** A site must fail several checks in a row before a DOWN alert fires, and you get exactly one alert per incident, followed by a recovery message with the total downtime. No flapping, no spam.
- **Content checks.** Optionally require a keyword in the response body, so a 200 page that says "maintenance" still counts as a failure.
- **TLS certificate watch.** Get warned before a certificate expires.
- **Rich Discord embeds** with reason, failed-check count, response time and downtime. Rate limits (HTTP 429) are handled automatically.
- **`status.json` output** with status, latency, uptime percentage and recent response times, ready for any dashboard.
- **Persistent state** survives restarts, so a reboot never re-sends old alerts.
- **Rotating logs** (`uptime.log`) and graceful shutdown on Ctrl+C or SIGTERM.
- **CI-friendly.** `--once` runs one cycle and exits non-zero if anything is failing.
- **Secrets stay out of code.** The webhook comes from an environment variable and is never logged.

## Quick start

```bash
git clone https://github.com/JudeaTablate/sentinel-uptime-monitor.git
cd sentinel-uptime-monitor
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env          # then paste your webhook URL into .env
python monitor.py --test-alert   # confirm Discord is wired up
python monitor.py                # start monitoring
```

Edit `config.yaml` to choose what to watch.

## Configuration

### Secrets (environment)

| Variable | Required | Description |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | No | Discord webhook for alerts. If unset, Sentinel still monitors and logs, but sends nothing. |

Put it in a local `.env` file (git-ignored) or set it in your host, container or CI secret store. Never put it in `config.yaml` or source code.

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
| `--test-alert` | Send a test message to Discord and exit. |
| `-v, --verbose` | Debug output. |

## How alerting works

```
check -> failed? -> count consecutive failures -> reached threshold? -> DOWN alert (once)
                 \-> succeeded after DOWN -> RECOVERED alert with downtime
```

State lives in `state.json`; the latest summary for dashboards lives in `status.json`.

## Running it for real

**systemd (Linux)**

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

**GitHub Actions (no server)**: add `DISCORD_WEBHOOK_URL` under *Settings → Secrets and variables → Actions* and run `python monitor.py --once` on a `schedule:` trigger.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers config validation, alert state transitions, retries, Discord rate limiting, and a guard test that fails if a webhook-looking URL ever lands in the repo.

## Project layout

| Path | Purpose |
| --- | --- |
| `monitor.py` | The monitor: config, checks, state, alerts, CLI |
| `config.yaml` | Sites and settings (no secrets) |
| `.env.example` | Template for local secrets |
| `tests/` | Test suite |
| `docs/` | Project website (deployed with GitHub Pages) |
| `.github/workflows/` | CI, secret scanning and site deployment |

## Security

See [SECURITY.md](SECURITY.md). In short: secrets come from the environment, the webhook URL is validated and never logged, and CI scans every push for leaked credentials.

## License

MIT
