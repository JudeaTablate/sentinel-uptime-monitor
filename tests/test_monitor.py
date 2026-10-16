import json
import re
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import monitor  # noqa: E402

FAKE_WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/fake-token_for-tests"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(monitor, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(monitor, "ssl_days_left", lambda *_a, **_k: None)


def result(ok=True, error=None, code=200, name="Site"):
    return monitor.CheckResult(name, "https://example.com", ok, code, 42.0, error, monitor.utc_now())


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def _next(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    get = post = lambda self, *a, **k: self._next()


# ---------------------------------------------------------------- config ---
def test_parse_config_defaults_and_sites():
    settings, sites = monitor.parse_config({"sites": [{"name": "A", "url": "https://a.test"}]})
    assert settings.interval == 60
    assert sites[0].expected_status == (200,)


def test_parse_config_accepts_status_list_and_options():
    _, sites = monitor.parse_config(
        {"sites": [{"url": "https://a.test", "expected_status": [200, 204], "timeout": 3, "keyword": "ok"}]}
    )
    assert sites[0].expected_status == (200, 204)
    assert sites[0].timeout == 3.0
    assert sites[0].name == "a.test"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"sites": []},
        {"sites": [{"name": "A"}]},
        {"sites": [{"url": "ftp://a.test"}]},
        {"sites": [{"url": "https://a.test"}, {"url": "https://a.test"}]},
        {"settings": {"interval": -5}, "sites": [{"url": "https://a.test"}]},
        {"settings": {"bogus": 1}, "sites": [{"url": "https://a.test"}]},
    ],
)
def test_parse_config_rejects_bad_input(raw):
    with pytest.raises(monitor.ConfigError):
        monitor.parse_config(raw)


def test_load_config_missing_file(tmp_path):
    with pytest.raises(monitor.ConfigError):
        monitor.load_config(tmp_path / "nope.yaml")


def test_shipped_config_is_valid():
    settings, sites = monitor.load_config(ROOT / "config.yaml")
    assert sites and settings.interval > 0


# ----------------------------------------------------------------- state ---
def test_down_alert_only_after_threshold():
    state = monitor.SiteState()
    monitor.apply_result(state, result(), 2)
    assert monitor.apply_result(state, result(False, "HTTP 500", 500), 2) is None
    assert state.label == "degraded"
    assert monitor.apply_result(state, result(False, "HTTP 500", 500), 2) == "down"
    assert state.label == "down"


def test_no_repeat_alerts_while_down_then_recovery():
    state = monitor.SiteState()
    for _ in range(2):
        monitor.apply_result(state, result(False, "x", None), 2)
    assert monitor.apply_result(state, result(False, "x", None), 2) is None
    assert monitor.apply_result(state, result(), 2) == "recovered"
    assert state.label == "operational"
    assert state.consecutive_failures == 0


def test_single_blip_never_alerts():
    state = monitor.SiteState()
    monitor.apply_result(state, result(), 2)
    assert monitor.apply_result(state, result(False, "x", None), 2) is None
    assert monitor.apply_result(state, result(), 2) is None


def test_uptime_and_latency_history_are_bounded():
    state = monitor.SiteState()
    for _ in range(50):
        monitor.apply_result(state, result(), 2)
    monitor.apply_result(state, result(False, "x", None), 2)
    assert state.uptime_percent == round(100 * 50 / 51, 2)
    assert len(state.latencies) == monitor.LATENCY_HISTORY


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = monitor.SiteState()
    monitor.apply_result(state, result(), 2)
    monitor.save_state(path, {"Site": state})
    assert monitor.load_state(path)["Site"].checks_ok == 1


def test_corrupt_state_file_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    assert monitor.load_state(path) == {}


def test_human_duration():
    assert monitor.human_duration("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:45+00:00") == "45s"
    assert monitor.human_duration("2026-01-01T00:00:00+00:00", "2026-01-02T03:05:00+00:00") == "1d 3h 5m"
    assert monitor.human_duration(None) == "unknown"


# ---------------------------------------------------------------- checks ---
SETTINGS = monitor.Settings(retries=1)
SITE = monitor.Site("S", "https://example.com")


def test_check_site_success():
    out = monitor.check_site(SITE, SETTINGS, FakeSession(FakeResponse(200)))
    assert out.ok and out.status_code == 200 and out.latency_ms is not None


def test_check_site_retries_then_succeeds():
    session = FakeSession(requests.ConnectionError("boom"), FakeResponse(200))
    assert monitor.check_site(SITE, SETTINGS, session).ok
    assert session.calls == 2


def test_check_site_reports_wrong_status():
    out = monitor.check_site(SITE, monitor.Settings(retries=0), FakeSession(FakeResponse(503)))
    assert not out.ok and out.error == "HTTP 503"


def test_check_site_accepts_custom_status():
    site = monitor.Site("S", "https://example.com", expected_status=(404,))
    assert monitor.check_site(site, monitor.Settings(retries=0), FakeSession(FakeResponse(404))).ok


def test_check_site_keyword():
    site = monitor.Site("S", "https://example.com", keyword="healthy")
    bad = monitor.check_site(site, monitor.Settings(retries=0), FakeSession(FakeResponse(200, "broken")))
    good = monitor.check_site(site, monitor.Settings(retries=0), FakeSession(FakeResponse(200, "all healthy")))
    assert not bad.ok and good.ok


def test_check_site_connection_error_names_exception_only():
    out = monitor.check_site(SITE, monitor.Settings(retries=0), FakeSession(requests.Timeout("secret detail")))
    assert out.error == "Timeout" and out.status_code is None


# --------------------------------------------------------------- discord ---
def test_notifier_rejects_non_discord_urls():
    for bad in ("", "http://discord.com/api/webhooks/1/a", "https://evil.test/api/webhooks/1/a"):
        with pytest.raises(monitor.ConfigError):
            monitor.DiscordNotifier(bad)


def test_notifier_from_env(monkeypatch):
    monkeypatch.delenv(monitor.WEBHOOK_ENV_VAR, raising=False)
    assert monitor.DiscordNotifier.from_env() is None
    monkeypatch.setenv(monitor.WEBHOOK_ENV_VAR, FAKE_WEBHOOK)
    assert monitor.DiscordNotifier.from_env() is not None


def test_notifier_retries_on_rate_limit():
    session = FakeSession(FakeResponse(429, payload={"retry_after": 0.1}), FakeResponse(204))
    assert monitor.DiscordNotifier(FAKE_WEBHOOK, session).send({"title": "t"})
    assert session.calls == 2


def test_notifier_failure_does_not_leak_url(caplog):
    session = FakeSession(FakeResponse(404))
    with caplog.at_level("ERROR", logger="sentinel"):
        assert not monitor.DiscordNotifier(FAKE_WEBHOOK, session).send({"title": "t"})
    assert "fake-token" not in caplog.text and "123456789012345678" not in caplog.text


def test_notifier_network_error_is_handled():
    session = FakeSession(requests.ConnectionError("down"))
    assert not monitor.DiscordNotifier(FAKE_WEBHOOK, session).send({"title": "t"})


# ----------------------------------------------------------------- cycle ---
class RecordingNotifier:
    def __init__(self):
        self.embeds = []

    def send(self, embed):
        self.embeds.append(embed)
        return True


def test_run_cycle_alerts_once_and_writes_status(tmp_path):
    settings = monitor.Settings(
        retries=0,
        failure_threshold=2,
        state_file=str(tmp_path / "state.json"),
        status_file=str(tmp_path / "status.json"),
    )
    sites = [monitor.Site("S", "https://example.com")]
    states, notifier = {}, RecordingNotifier()

    failing = FakeSession(FakeResponse(500), FakeResponse(500), FakeResponse(500))
    for _ in range(3):
        monitor.run_cycle(sites, settings, states, notifier, failing)
    assert [e["title"] for e in notifier.embeds] == ["🔴 S is DOWN"]

    monitor.run_cycle(sites, settings, states, notifier, FakeSession(FakeResponse(200)))
    assert notifier.embeds[-1]["title"] == "🟢 S has recovered"

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["sites"][0]["status"] == "operational"
    assert (tmp_path / "state.json").exists()


def test_certificate_warning_is_sent_once(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "ssl_days_left", lambda *_a, **_k: 3)
    settings = monitor.Settings(
        retries=0, state_file=str(tmp_path / "s.json"), status_file=str(tmp_path / "st.json")
    )
    sites, states, notifier = [monitor.Site("S", "https://example.com")], {}, RecordingNotifier()
    for _ in range(3):
        monitor.run_cycle(sites, settings, states, notifier, FakeSession(FakeResponse(200)))
    assert sum("certificate" in e["title"] for e in notifier.embeds) == 1


# ---------------------------------------------------------- repo hygiene ---
def test_no_webhook_urls_or_tokens_in_tracked_source():
    """Guards against ever committing a real webhook again."""
    pattern = re.compile(r"discord(?:app)?\.com/api/webhooks/\d{15,}/[\w-]{20,}")
    skip = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}
    offenders = []
    for path in ROOT.rglob("*"):
        if path.is_dir() or skip & set(path.parts) or path.name == ".env":
            continue
        if path.name == "test_monitor.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"webhook-looking URL found in: {offenders}"


def test_env_files_are_gitignored():
    ignore = (ROOT / ".gitignore").read_text()
    assert ".env" in ignore.splitlines()
