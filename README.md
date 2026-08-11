# cursor-cli-usage

Cursor account usage and quota monitor. It follows the same dependency-free
interface as the other `/code/*usage` tools.

The tool targets the Cursor CLI harness, but reports account-wide Cursor quota.
It does not estimate usage from local CLI sessions.

## Example output

```text
Cursor usage
Status: live
Plan: 6.1% used
Resets: 1787506237000
```

Statusline:

```text
cursor:q:6.1%used reset:12d20h
```

## Install

```bash
uv tool install cursor-cli-usage
```

For local development:

```bash
uv tool install .
```

## Commands

| Command | Description |
| --- | --- |
| `cursor-cli-usage` | Show current account usage |
| `cursor-cli-usage status` | Same as above |
| `cursor-cli-usage json` | Print normalized JSON |
| `cursor-cli-usage statusline` | Print compact cached output |
| `cursor-cli-usage refresh` | Refresh the cache and print status |
| `cursor-cli-usage daemon [-i SECS]` | Keep the cache fresh |
| `cursor-cli-usage install` | Print installation instructions |

## Authentication and data

For every live request, the tool first asks the installed Cursor CLI for its
current authentication state with `agent status --format json`. If the CLI is
unavailable, it falls back to `cursorAuth/accessToken` in Cursor IDE's local
`state.vscdb`. It sends only the current access token to Cursor's dashboard
usage service. It never refreshes, rotates, or modifies Cursor-owned
credentials.

The normalized JSON includes:

- `provider`, `source`, `retrieved_at`, and `status`
- billing-cycle start and end
- plan and spend-limit usage
- models assigned to Cursor's automatic usage bucket

`status` is `live`, `cached`, `stale`, or `unavailable`. Errors and caches do
not include the access token or Cursor account identity.

The tool writes only its own non-secret cache at
`~/.cursor/usage-limits.json`. JSON results report whether they are `live`,
`cached`, `stale`, or `unavailable`.

Environment overrides:

- `CURSOR_USAGE_AGENT`: alternate Cursor CLI executable
- `CURSOR_USAGE_STATE_DB`: alternate Cursor `state.vscdb` path
- `CURSOR_USAGE_FILE`: alternate cache path

The usage endpoint is undocumented and may change when Cursor changes its
dashboard implementation.

## Options

```text
usage: cursor-cli-usage [-h] [-i INTERVAL] [--max-age MAX_AGE] [--refresh]
                        {status,json,daemon,statusline,refresh,install}
```

- `--max-age SECS`: maximum cache age used by `statusline`
- `--refresh`: bypass the cache for `statusline`
- `-i SECS`: daemon refresh interval

## Development

```bash
uv --no-config lock --check
uv --no-config run --locked python -m unittest discover -s tests
uv --no-config build --no-sources
```
