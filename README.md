# oc-relay

A localhost-only macOS relay that lets LM Studio's
`openai-compat-endpoint` plugin call OpenCode Go. It injects the required stable
`x-opencode-session` header and forwards streaming responses incrementally.

## Install

Requires macOS and Python 3. No third-party packages are used.

```bash
./scripts/install.sh
```

The installer creates:

- `~/.local/bin/oc-relay`
- `~/.local/lib/oc-relay/oc_relay.py`
- `~/.config/oc-relay/config.json`
- `~/Library/LaunchAgents/com.gabrielblacher.oc-relay.plist`
- `~/Library/Logs/oc-relay.log`

Add `~/.local/bin` to `PATH` if it is not already present. Point LM Studio's
Base URL to `http://127.0.0.1:8788`. Keep the API key in LM Studio; the relay
forwards it but never logs it. Use an exact ID from `oc-relay models` in LM
Studio's Custom Model field.

## Operate

```bash
oc-relay start
oc-relay stop
oc-relay restart
oc-relay status
oc-relay logs
oc-relay logs --follow
oc-relay models
```

`status` shows launchd state, health, port, PID, paths, and the last ten log
lines. The local endpoint `http://127.0.0.1:8788/health` returns JSON health
information.

## Configure

Edit `~/.config/oc-relay/config.json`, then run `oc-relay restart`:

```json
{
  "port": 8788,
  "target_url": "https://opencode.ai/zen/go/v1",
  "fallback_model": null
}
```

`fallback_model` is used only when a JSON request has no non-empty `model`
field. LM Studio's explicit Custom Model value always wins.

## Uninstall

```bash
./scripts/uninstall.sh
```

The default uninstall preserves the config and log. To remove those too, use
`./scripts/uninstall.sh --purge`.

## Validate

```bash
./scripts/check.sh
~/.local/bin/oc-relay status
~/.local/bin/oc-relay models
curl --fail --silent http://127.0.0.1:8788/health
```

Task state is tracked in [Issue #1](https://github.com/gblacher2/oc-relay/issues/1)
and [PR #2](https://github.com/gblacher2/oc-relay/pull/2).
