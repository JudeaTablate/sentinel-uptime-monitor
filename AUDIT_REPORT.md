# Sentinel Uptime Monitor — Audit and Change Notes

## Changes in this package

- Removed the duplicate nested `sentinel-uptime-monitor/` project directory. It caused pytest to import two files named `test_monitor` and fail during test collection.
- Tightened per-site configuration validation. Invalid timeout values, empty/invalid HTTP status-code lists, status codes outside 100–599, and non-string keywords now produce a clear `ConfigError` rather than an unexpected exception or invalid configuration.
- Added regression tests for those invalid per-site settings.
- Updated the README to use the current Vercel documentation URL and clarified local setup commands for Windows PowerShell versus macOS/Linux.
- Removed the GitHub Pages deployment workflow because the documented site is hosted on Vercel. This avoids a permanently failing, unused deployment workflow.
- Simplified CI naming and made the Python test matrix fail independently for each version.
- Excluded `.git` metadata from this delivery archive. Do not distribute local Git metadata when sharing a source-code bundle.

## Validation performed locally

- `python -m pytest -q`: **39 passed**
- `python -m py_compile monitor.py`: **passed**

These checks validate the code and tests in this package. They do not prove that the Vercel deployment or a real Discord webhook works in your account; test those separately after configuring your own secret.

## Important security action

A previous commit in the public repository contained a Discord webhook URL. Revoke/delete that webhook in Discord and create a new one before using alerts. Removing a value from the latest source file does not revoke a credential or remove it from Git history. Put the replacement in `.env` locally or in your hosting provider's secret settings. Never commit the real `.env` file.

## Suggested GitHub update

1. Back up your existing repository folder.
2. Extract this package.
3. Copy the package contents into the repository root, replacing the old files. Ensure the nested `sentinel-uptime-monitor/` directory is gone.
4. Keep your local `.git` folder when updating an existing clone; this download intentionally does not contain one.
5. Run `python -m pytest -q`.
6. Commit and push the changes.
7. Check the Actions tab. Both the Python test matrix and secret scan should pass.
8. Verify the Vercel docs URL opens and the Quick Start instructions match your checked-in files.
