import json
import smtplib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import monitor  # noqa: E402

ENV_KEYS = (
    "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM", "SMTP_HOST",
    "SMTP_PORT", "DISCORD_WEBHOOK_URL", "SENTINEL_CONFIG", "SENTINEL_STATE_DIR",
)
EMAIL_ENV = {
    "SMTP_USER": "owner@example.com",
    "SMTP_PASSWORD": "abcd efgh ijkl mnop",
    "ALERT_EMAIL_TO": "owner@example.com",
}
CONFIG = "settings:\n  failure_threshold: 1\n  retries: 0\nsites:\n  - name: Demo\n    url: https://demo.example\n"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(monitor, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_sleep", lambda _s: None)
    yield
    monitor.LOG.handlers.clear()


def use_email(monkeypatch, **extra):
    for key, value in {**EMAIL_ENV, **extra}.items():
        monkeypatch.setenv(key, value)


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def down_embed():
    result = monitor.CheckResult("Demo", "https://demo.example", False, 500, None, "HTTP 500", monitor.utc_now())
    state = monitor.SiteState(consecutive_failures=2)
    return monitor.down_embed(result, state)


# ---------------------------------------------------------------- email ---
def test_email_is_off_without_settings():
    assert monitor.EmailNotifier.from_env() is None


def test_email_with_partial_settings_is_off(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "owner@example.com")
    assert monitor.EmailNotifier.from_env() is None


def test_email_defaults_and_multiple_recipients(monkeypatch):
    use_email(monkeypatch, ALERT_EMAIL_TO="a@example.com; b@example.com")
    notifier = monitor.EmailNotifier.from_env()
    assert (notifier._host, notifier._port) == ("smtp.gmail.com", 465)
    assert notifier._recipients == ["a@example.com", "b@example.com"]


@pytest.mark.parametrize("extra", [{"ALERT_EMAIL_TO": "not-an-address"}, {"SMTP_PORT": "abc"}])
def test_email_rejects_bad_settings(monkeypatch, extra):
    use_email(monkeypatch, **extra)
    with pytest.raises(monitor.ConfigError):
        monitor.EmailNotifier.from_env()


def test_email_sends_over_ssl(monkeypatch, smtp):
    use_email(monkeypatch)
    assert monitor.EmailNotifier.from_env().send(down_embed())
    server = smtp.instances[0]
    assert server.port == 465 and not server.started_tls
    assert server.logged_in == ("owner@example.com", "abcd efgh ijkl mnop")
    message = server.sent[0]
    assert message["Subject"] == "[Sentinel] \U0001F534 Demo is DOWN"
    assert message["To"] == "owner@example.com"
    body = message.get_content()
    assert "https://demo.example" in body and "Reason: HTTP 500" in body


def test_email_uses_starttls_on_other_ports(monkeypatch, smtp):
    use_email(monkeypatch, SMTP_PORT="587")
    assert monitor.EmailNotifier.from_env().send(down_embed())
    assert smtp.instances[0].started_tls


def test_email_subject_stays_on_one_line(monkeypatch, smtp):
    use_email(monkeypatch)
    monitor.EmailNotifier.from_env().send({"title": "line one\nBcc: evil@example.com"})
    assert "\n" not in smtp.instances[0].sent[0]["Subject"]


def test_email_login_failure_is_reported_without_secrets(monkeypatch, caplog):
    class Rejecting(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"nope")

    monkeypatch.setattr(smtplib, "SMTP_SSL", Rejecting)
    use_email(monkeypatch)
    with caplog.at_level("ERROR", logger="sentinel"):
        assert not monitor.EmailNotifier.from_env().send(down_embed())
    assert "App Password" in caplog.text
    assert "abcd efgh" not in caplog.text


def test_email_network_error_is_handled(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise OSError("connection refused to secret-host")

    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)
    use_email(monkeypatch)
    with caplog.at_level("ERROR", logger="sentinel"):
        assert not monitor.EmailNotifier.from_env().send(down_embed())
    assert "OSError" in caplog.text and "secret-host" not in caplog.text


class Stub:
    def __init__(self, ok):
        self.ok, self.calls = ok, 0

    def send(self, embed):
        self.calls += 1
        return self.ok


def test_multi_notifier_tries_every_channel():
    first, second = Stub(False), Stub(True)
    multi = monitor.MultiNotifier([first, second])
    assert multi.send({"title": "t"}) is True
    assert (first.calls, second.calls) == (1, 1)
    assert not monitor.MultiNotifier([])


# --------------------------------------------------------------- config ---
def test_load_config_text_reports_bad_yaml_briefly():
    with pytest.raises(monitor.ConfigError, match="could not parse SENTINEL_CONFIG"):
        monitor.load_config_text("sites: [", "SENTINEL_CONFIG")


def test_relocate_state_moves_files_into_directory(tmp_path):
    moved = monitor.relocate_state(monitor.Settings(), tmp_path / "data")
    assert Path(moved.state_file) == tmp_path / "data" / "state.json"
    assert Path(moved.status_file) == tmp_path / "data" / "status.json"


def test_shipped_config_does_not_list_private_projects():
    text = (ROOT / "config.yaml").read_text(encoding="utf-8").lower()
    assert "vercel.app" not in text and "onrender.com" not in text


# ------------------------------------------------------------ end to end ---
def run_main(monkeypatch, tmp_path, ok, *args):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SENTINEL_CONFIG", CONFIG)
    monkeypatch.setenv("SENTINEL_STATE_DIR", str(tmp_path / "nested" / "state"))

    def fake_check(site, settings, session=None):
        return monitor.CheckResult(
            site.name, site.url, ok, 200 if ok else None, 42.0 if ok else None,
            None if ok else "ConnectionError", monitor.utc_now(),
        )

    monkeypatch.setattr(monitor, "check_site", fake_check)
    return monitor.main(["--once", *args])


def test_private_config_state_and_email_across_runs(monkeypatch, tmp_path, smtp):
    use_email(monkeypatch)

    assert run_main(monkeypatch, tmp_path, False) == 1  # outage begins: one DOWN email
    assert [m["Subject"] for s in smtp.instances for m in s.sent] == ["[Sentinel] \U0001F534 Demo is DOWN"]

    assert run_main(monkeypatch, tmp_path, False) == 1  # still down: no second email
    assert sum(len(s.sent) for s in smtp.instances) == 1

    assert run_main(monkeypatch, tmp_path, True) == 0  # recovery email
    subjects = [m["Subject"] for s in smtp.instances for m in s.sent]
    assert subjects[-1] == "[Sentinel] \U0001F7E2 Demo has recovered" and len(subjects) == 2

    status = json.loads((tmp_path / "nested" / "state" / "status.json").read_text())
    assert [site["name"] for site in status["sites"]] == ["Demo"]
    assert status["sites"][0]["status"] == "operational"
    assert not (tmp_path / "state.json").exists()  # nothing written outside the state dir


def test_test_alert_reports_each_channel(monkeypatch, tmp_path, smtp, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SENTINEL_CONFIG", CONFIG)
    monkeypatch.setenv("SENTINEL_STATE_DIR", str(tmp_path))
    use_email(monkeypatch)
    assert monitor.main(["--test-alert"]) == 0
    assert "Email: sent" in capsys.readouterr().out


def test_test_alert_without_channels_fails_clearly(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SENTINEL_CONFIG", CONFIG)
    monkeypatch.setenv("SENTINEL_STATE_DIR", str(tmp_path))
    assert monitor.main(["--test-alert"]) == 2
    assert "No alert channel" in capsys.readouterr().err
