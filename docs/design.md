# Launchd relay design

Issue: [#1](https://github.com/gblacher2/oc-relay/issues/1)

The installed service consists of an immutable copy of the Python relay under
`~/.local/lib/oc-relay`, a management command at `~/.local/bin/oc-relay`, a JSON
configuration file at `~/.config/oc-relay/config.json`, and the
`com.gabrielblacher.oc-relay` LaunchAgent. The service writes operational events
to `~/Library/Logs/oc-relay.log` and never writes HTTP headers or bodies.

The Python process creates one UUID session identifier at startup. Every proxied
request receives that identifier through `x-opencode-session`. A threaded HTTP
server accepts only loopback connections. Successful upstream responses are read
and flushed incrementally so server-sent events reach LM Studio without full
response buffering.

The CLI derives all paths from the current user's home directory. Its service
commands use launchd's per-user GUI domain. `status` combines launchd state, the
configured port, a `/health` probe, and recent log output. `models` queries the
configured upstream `/models` endpoint and prints sorted model identifiers.

Installation renders the LaunchAgent from a tracked template with absolute local
paths, bootstraps it, and verifies health. Uninstallation boots out the service
and removes only files owned by this project; it preserves the operator's config
and log unless explicitly requested in a future change.
