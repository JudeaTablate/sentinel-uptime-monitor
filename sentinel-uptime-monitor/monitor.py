#!/usr/bin/env python3
"""Sentinel: a lightweight uptime monitor with Discord alerting.

Checks a list of HTTP(S) endpoints on a schedule, tracks their state, and sends
a Discord message only when something changes (down, recovered, certificate
expiring). Results are also written to ``status.json`` for dashboards.

Secrets are never stored in this file or in config. The Discord webhook is read
from the ``DISCORD_WEBHOOK_URL`` environment variable (or a local ``.env``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import socket
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from dotenv import load_dotenv

__version__ = "2.0.0"

LOG = logging.getLogger("sentinel")
USER_AGENT = f"Sentinel-Uptime-Monitor/{__version__}"
WEBHOOK_ENV_VAR = "DISCORD_WEBHOOK_URL"
WEBHOOK_PATTERN = re.compile(
    r"^https://(?:(?:canary|ptb)\.)?(?:discord|discordapp)\.com/api/(?:v\d+/)?webhooks/\d+/[\w-]+/?$"
)
LATENCY_HISTORY = 30

COLOR_DOWN = 0xE5484D
COLOR_UP = 0x30A46C
COLOR_WARN = 0xF5A524

_sleep = time.sleep  # indirection so tests can skip real waiting


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class ConfigError(ValueError):
    """Raised when the configuration file is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    interval: int = 60
    timeout: float = 10.0
    retries: int = 1
    failure_threshold: int = 2
    workers: int = 8
    ssl_warn_days: int = 14
    log_file: str = "uptime.log"
    state_file: str = "state.json"
    status_file: str = "status.json"


@dataclass(frozen=True)
class Site:
    name: str
    url: str
    expected_status: tuple[int, ...] = (200,)
    timeout: float | None = None
    keyword: str | None = None


def _positive(value, field_name: str, kind=int):
    try:
        number = kind(value)
    except (TypeError, ValueError):
        raise ConfigError(f"settings.{field_name} must be a number") from None
    if number < 0 or (number == 0 and field_name != "retries"):
        raise ConfigError(f"settings.{field_name} must be positive")
    return number


def parse_config(raw: dict) -> tuple[Settings, list[Site]]:
    """Validate a parsed YAML document and return typed settings and sites."""
    if not isinstance(raw, dict):
        raise ConfigError("config must be a mapping with 'settings' and 'sites'")

    given = raw.get("settings") or {}
    defaults = Settings()
    unknown = set(given) - set(asdict(defaults))
    if unknown:
        raise ConfigError(f"unknown settings: {', '.join(sorted(unknown))}")

    settings = Settings(
        interval=_positive(given.get("interval", defaults.interval), "interval"),
        timeout=_positive(given.get("timeout", defaults.timeout), "timeout", float),
        retries=_positive(given.get("retries", defaults.retries), "retries"),
        failure_threshold=_positive(
            given.get("failure_threshold", defaults.failure_threshold), "failure_threshold"
        ),
        workers=_positive(given.get("workers", defaults.workers), "workers"),
        ssl_warn_days=_positive(given.get("ssl_warn_days", defaults.ssl_warn_days), "ssl_warn_days"),
        log_file=str(given.get("log_file", defaults.log_file)),
        state_file=str(given.get("state_file", defaults.state_file)),
        status_file=str(given.get("status_file", defaults.status_file)),
    )

    entries = raw.get("sites")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("config needs a non-empty 'sites' list")

    sites: list[Site] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or "url" not in entry:
            raise ConfigError(f"site #{index} must be a mapping with a 'url'")
        url = str(entry["url"]).strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConfigError(f"site #{index}: url must start with http:// or https://")
        name = str(entry.get("name") or parsed.netloc)
        if name in seen:
            raise ConfigError(f"duplicate site name: {name}")
        seen.add(name)

        expected = entry.get("expected_status", 200)
        codes = expected if isinstance(expected, list) else [expected]
        try:
            expected_status = tuple(int(code) for code in codes)
        except (TypeError, ValueError):
            raise ConfigError(f"site '{name}': expected_status must be integers") from None

        timeout = entry.get("timeout")
        sites.append(
            Site(
                name=name,
                url=url,
                expected_status=expected_status,
                timeout=float(timeout) if timeout is not None else None,
                keyword=entry.get("keyword"),
            )
        )
    return settings, sites


def load_config(path: str | Path) -> tuple[Settings, list[Site]]:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from None
    return parse_config(raw)


# --------------------------------------------------------------------------- #
# Checking
# --------------------------------------------------------------------------- #
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class CheckResult:
    name: str
    url: str
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None
    checked_at: str
    ssl_days_left: int | None = None


def ssl_days_left(url: str, timeout: float) -> int | None:
    """Days until the site's TLS certificate expires, or None if unknown."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    try:
        context = ssl.create_default_context()
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=parsed.hostname) as tls:
                cert = tls.getpeercert()
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)
        return (expires - datetime.now(timezone.utc)).days
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return None


def check_site(site: Site, settings: Settings, session: requests.Session | None = None) -> CheckResult:
    """Request a site, retrying on failure, and describe the outcome."""
    session = session or requests.Session()
    timeout = site.timeout or settings.timeout
    result: CheckResult | None = None

    for attempt in range(settings.retries + 1):
        started = time.perf_counter()
        try:
            response = session.get(
                site.url,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=True,
            )
            latency = round((time.perf_counter() - started) * 1000, 1)
            ok, error = True, None
            if response.status_code not in site.expected_status:
                ok, error = False, f"HTTP {response.status_code}"
            elif site.keyword and site.keyword not in response.text:
                ok, error = False, "expected keyword not found in response"
            result = CheckResult(
                site.name, site.url, ok, response.status_code, latency, error, utc_now()
            )
        except requests.RequestException as exc:
            result = CheckResult(
                site.name, site.url, False, None, None, type(exc).__name__, utc_now()
            )
        if result.ok:
            break
        if attempt < settings.retries:
            _sleep(min(2**attempt, 5))

    assert result is not None
    if result.ok:
        result.ssl_days_left = ssl_days_left(site.url, timeout)
    return result


# --------------------------------------------------------------------------- #
# State tracking
# --------------------------------------------------------------------------- #
@dataclass
class SiteState:
    up: bool | None = None
    consecutive_failures: int = 0
    since: str | None = None
    checks_total: int = 0
    checks_ok: int = 0
    last_error: str | None = None
    last_status_code: int | None = None
    ssl_alerted: bool = False
    latencies: list[float] = field(default_factory=list)

    @property
    def uptime_percent(self) -> float | None:
        if not self.checks_total:
            return None
        return round(100 * self.checks_ok / self.checks_total, 2)

    @property
    def label(self) -> str:
        if self.up is False:
            return "down"
        if self.consecutive_failures:
            return "degraded"
        return "operational" if self.up else "unknown"


def apply_result(state: SiteState, result: CheckResult, threshold: int) -> str | None:
    """Update state with a check result. Returns 'down', 'recovered' or None."""
    state.checks_total += 1
    state.last_status_code = result.status_code

    if result.ok:
        previous = state.up
        state.checks_ok += 1
        state.consecutive_failures = 0
        state.last_error = None
        state.up = True
        if previous is not True:
            state.since = result.checked_at
        if result.latency_ms is not None:
            state.latencies = (state.latencies + [result.latency_ms])[-LATENCY_HISTORY:]
        return "recovered" if previous is False else None

    state.consecutive_failures += 1
    state.last_error = result.error
    if state.consecutive_failures >= threshold and state.up is not False:
        state.up = False
        state.since = result.checked_at
        return "down"
    return None


def load_state(path: str | Path) -> dict[str, SiteState]:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {name: SiteState(**values) for name, values in raw.items()}
    except (OSError, ValueError, TypeError):
        LOG.warning("Could not read %s; starting with a clean state", path)
        return {}


def _atomic_write(path: str | Path, payload: dict) -> None:
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def save_state(path: str | Path, states: dict[str, SiteState]) -> None:
    _atomic_write(path, {name: asdict(state) for name, state in states.items()})


def write_status(
    path: str | Path,
    results: list[CheckResult],
    states: dict[str, SiteState],
    settings: Settings,
) -> None:
    """Write a dashboard-friendly summary of the latest cycle."""
    by_name = {result.name: result for result in results}
    sites = []
    for name, state in states.items():
        result = by_name.get(name)
        sites.append(
            {
                "name": name,
                "url": result.url if result else None,
                "status": state.label,
                "status_code": state.last_status_code,
                "latency_ms": result.latency_ms if result else None,
                "uptime_percent": state.uptime_percent,
                "since": state.since,
                "error": state.last_error,
                "ssl_days_left": result.ssl_days_left if result else None,
                "recent_latencies_ms": state.latencies,
            }
        )
    _atomic_write(
        path,
        {
            "generated_at": utc_now(),
            "interval_seconds": settings.interval,
            "sites": sites,
        },
    )


def human_duration(start_iso: str | None, end_iso: str | None = None) -> str:
    if not start_iso:
        return "unknown"
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso) if end_iso else datetime.now(timezone.utc)
    seconds = max(int((end - start).total_seconds()), 0)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{days}d" if days else "", f"{hours}h" if hours else "", f"{minutes}m" if minutes else ""]
    text = " ".join(part for part in parts if part)
    return text or f"{secs}s"


# --------------------------------------------------------------------------- #
# Discord notifications
# --------------------------------------------------------------------------- #
class DiscordNotifier:
    """Posts embeds to a Discord webhook. The URL is never logged."""

    def __init__(self, webhook_url: str, session: requests.Session | None = None):
        if not WEBHOOK_PATTERN.match(webhook_url):
            raise ConfigError(f"{WEBHOOK_ENV_VAR} is not a valid Discord webhook URL")
        self._url = webhook_url
        self._session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "DiscordNotifier | None":
        url = os.environ.get(WEBHOOK_ENV_VAR, "").strip()
        if not url:
            return None
        return cls(url)

    def send(self, embed: dict) -> bool:
        payload = {"username": "Sentinel", "embeds": [embed]}
        for _ in range(3):
            try:
                response = self._session.post(self._url, json=payload, timeout=10)
            except requests.RequestException as exc:
                LOG.error("Discord delivery failed: %s", type(exc).__name__)
                return False
            if response.status_code == 429:
                try:
                    wait = float(response.json().get("retry_after", 1))
                except ValueError:
                    wait = 1.0
                _sleep(min(wait, 10))
                continue
            if response.ok:
                return True
            hint = " (webhook deleted or revoked?)" if response.status_code in (401, 404) else ""
            LOG.error("Discord rejected the alert: HTTP %s%s", response.status_code, hint)
            return False
        LOG.error("Discord rate limit not cleared after retries")
        return False


def down_embed(result: CheckResult, state: SiteState) -> dict:
    return {
        "title": f"🔴 {result.name} is DOWN",
        "description": result.url,
        "color": COLOR_DOWN,
        "fields": [
            {"name": "Reason", "value": result.error or "unknown", "inline": True},
            {"name": "Failed checks", "value": str(state.consecutive_failures), "inline": True},
        ],
        "timestamp": result.checked_at,
    }


def recovered_embed(result: CheckResult, downtime: str) -> dict:
    fields = [{"name": "Downtime", "value": downtime, "inline": True}]
    if result.latency_ms is not None:
        fields.append({"name": "Response time", "value": f"{result.latency_ms:.0f} ms", "inline": True})
    return {
        "title": f"🟢 {result.name} has recovered",
        "description": result.url,
        "color": COLOR_UP,
        "fields": fields,
        "timestamp": result.checked_at,
    }


def ssl_embed(result: CheckResult) -> dict:
    return {
        "title": f"🟡 {result.name}: certificate expiring soon",
        "description": result.url,
        "color": COLOR_WARN,
        "fields": [{"name": "Days left", "value": str(result.ssl_days_left), "inline": True}],
        "timestamp": result.checked_at,
    }


# --------------------------------------------------------------------------- #
# Monitoring loop
# --------------------------------------------------------------------------- #
def run_cycle(
    sites: list[Site],
    settings: Settings,
    states: dict[str, SiteState],
    notifier: DiscordNotifier | None,
    session: requests.Session | None = None,
) -> list[CheckResult]:
    """Check every site once, update state, and send alerts for changes."""
    with ThreadPoolExecutor(max_workers=min(settings.workers, len(sites))) as pool:
        results = list(pool.map(lambda site: check_site(site, settings, session), sites))

    for result in results:
        state = states.setdefault(result.name, SiteState())
        was_down_since = state.since
        event = apply_result(state, result, settings.failure_threshold)

        if result.ok:
            detail = f"{result.status_code} in {result.latency_ms:.0f} ms"
            LOG.info("[OK] %s (%s)", result.name, detail)
        else:
            LOG.warning("[FAIL] %s: %s (%d in a row)", result.name, result.error, state.consecutive_failures)

        if event == "down":
            LOG.error("[DOWN] %s", result.name)
            if notifier:
                notifier.send(down_embed(result, state))
        elif event == "recovered":
            downtime = human_duration(was_down_since, result.checked_at)
            LOG.info("[RECOVERED] %s after %s", result.name, downtime)
            if notifier:
                notifier.send(recovered_embed(result, downtime))

        expiring = result.ssl_days_left is not None and result.ssl_days_left <= settings.ssl_warn_days
        if expiring and not state.ssl_alerted:
            state.ssl_alerted = True
            LOG.warning("[SSL] %s certificate expires in %d days", result.name, result.ssl_days_left)
            if notifier:
                notifier.send(ssl_embed(result))
        elif not expiring and result.ssl_days_left is not None:
            state.ssl_alerted = False

    save_state(settings.state_file, states)
    write_status(settings.status_file, results, states, settings)
    return results


def setup_logging(log_file: str, verbose: bool) -> None:
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
    ):
        handler.setFormatter(formatter)
        LOG.addHandler(handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sentinel", description="Lightweight uptime monitor with Discord alerts.")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to the YAML config (default: config.yaml)")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit (exit code 1 if any site is failing)")
    parser.add_argument("--test-alert", action="store_true", help="send a test message to Discord and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug output")
    parser.add_argument("--version", action="version", version=f"sentinel {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()

    try:
        settings, sites = load_config(args.config)
        notifier = DiscordNotifier.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(settings.log_file, args.verbose)

    if args.test_alert:
        if not notifier:
            print(f"Set {WEBHOOK_ENV_VAR} first (see .env.example).", file=sys.stderr)
            return 2
        sent = notifier.send(
            {
                "title": "✅ Sentinel test alert",
                "description": "Your webhook is configured correctly.",
                "color": COLOR_UP,
                "timestamp": utc_now(),
            }
        )
        print("Test alert sent." if sent else "Test alert failed. Check the log.")
        return 0 if sent else 1

    if not notifier:
        LOG.warning("%s is not set: monitoring will run without Discord alerts", WEBHOOK_ENV_VAR)

    states = load_state(settings.state_file)
    states = {name: state for name, state in states.items() if name in {site.name for site in sites}}
    session = requests.Session()

    if args.once:
        results = run_cycle(sites, settings, states, notifier, session)
        return 0 if all(result.ok for result in results) else 1

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    LOG.info("Sentinel %s watching %d site(s) every %ds", __version__, len(sites), settings.interval)
    while not stop.is_set():
        run_cycle(sites, settings, states, notifier, session)
        stop.wait(settings.interval)
    LOG.info("Shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
