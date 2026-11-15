# Security

## Handling secrets

- The Discord webhook URL is read from the `DISCORD_WEBHOOK_URL` environment variable, either from your shell, your host's secret store, or a local `.env` file.
- `.env` is listed in `.gitignore`. Only `.env.example` (with an empty value) belongs in the repository.
- Sentinel validates that the value is a genuine `discord.com` webhook URL and never writes it to logs or error messages.
- A test (`test_no_webhook_urls_or_tokens_in_tracked_source`) and a CI secret scan fail the build if a webhook-looking URL is committed.

## If a webhook leaks

Deleting it in a new commit does **not** make it safe: the value stays in git history and may already have been scraped.

1. In Discord, open **Server Settings → Integrations → Webhooks**, delete the exposed webhook, and create a new one.
2. Put the new URL in your environment or `.env`, never in code.
3. Optionally rewrite history (for example with `git filter-repo`) to remove the old value. This is housekeeping only; the revoked URL is harmless once deleted.

## Reporting a vulnerability

Please open a private security advisory on GitHub (**Security → Report a vulnerability**) rather than a public issue.

## Email credentials

Email alerts use an SMTP login (for Gmail, an App Password). Set `SMTP_USER`, `SMTP_PASSWORD` and `ALERT_EMAIL_TO` as environment variables or GitHub Actions secrets, never in code or `config.yaml`. Sentinel logs only the exception type when delivery fails, never the password or server details. If an App Password leaks, revoke it at https://myaccount.google.com/apppasswords and create a new one.
