# When it goes wrong

## A profile says it is signed out, but it is signed in

Almost always `CLAUDE_CONFIG_DIR` set to `~/.claude`. Unset it for the default
profile. See `setup.md`.

The desktop app and the CLI also keep **separate** credentials for the same
profile: the app refreshes its own token, the CLI uses the Keychain entry. So the
CLI can be expired while the app works perfectly. Fix with `claude auth login` in
that profile; it does not disturb the app.

## The sign-in put the wrong account in the profile

See `setup.md`. Back up `~/.claude.json` first, open the consent URL in the right
browser profile yourself, and check `auth status` afterwards.

## A browser crashes when a worker runs

Expected, and already handled. A sandboxed worker cannot launch a browser: it
aborts at startup because it cannot reach the window server, and the operating
system shows a "quit unexpectedly" dialog. Setup probes this and the runner warns
the worker off. If it still happens:

```bash
python3 scripts/setup.py --show | jq '.codex'
python3 scripts/probe.py
```

If `can_launch_browser` is false, keep browser work on the primary session. Do not
"fix" it by disabling the worker's sandbox: that trades a dialog for unrestricted
disk access.

If a project has its own browser QA harness, put a line in that project's
instructions saying which runtime may run it. The worker reads those instructions
and will otherwise follow them into the crash.

## A delegation exited zero but nothing happened

The likeliest failures are a bad flag and a refusal that reads like prose. Both
leave a normal-looking reply. The exchange file holds the full text:

```bash
python3 scripts/report.py 7
```

Read the reply, not the exit code. In `--work` mode, verify the artefact.

## The status bar is blank, or shows no percentages

The bar appears after a restart. The limit figures only exist once the account has
had a reply in that session, and they are absent entirely for accounts the API does
not report limits for. No cached figure means the usage guard stays quiet, which is
deliberate: acting on a stale number is worse than not acting.

## Failover will not fire

In order: `failover.enabled` true in the config; no `~/.second-wind/no-failover`
file; `~/.second-wind/usage-primary.json` exists and is under an hour old; the
percentage is genuinely at or over the threshold. The guard ignores readings older
than an hour because a window may have reset since.

## Undo everything

```bash
python3 scripts/setup.py --uninstall
rm -rf ~/.second-wind
```

That restores both settings files from the backups it made and leaves the accounts
themselves alone.
