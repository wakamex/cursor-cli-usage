# cursor-cli-usage

Cursor account usage and quota monitor. It follows the same dependency-free
interface as the other `/code/*usage` tools.

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

The tool reads `cursorAuth/accessToken` from Cursor's local
`state.vscdb` and sends it to Cursor's current-period dashboard usage service.
It rereads the database for every live request. It never reads the refresh
token, refreshes credentials, or modifies Cursor-owned files.

The tool writes only its own non-secret cache at
`~/.cursor/usage-limits.json`. JSON results report whether they are `live`,
`cached`, `stale`, or `unavailable`.

Environment overrides:

- `CURSOR_USAGE_STATE_DB`: alternate Cursor `state.vscdb` path
- `CURSOR_USAGE_FILE`: alternate cache path

The usage endpoint is undocumented and may change when Cursor changes its
dashboard implementation.
