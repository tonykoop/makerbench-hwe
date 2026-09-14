# Arena Studio

Arena Studio is MakerBench's local web cockpit for the Code-CAD A/B arena:
run discovery, dry-run launches with live logs, blind voting, agreement
analytics, DoE queue building, the nightly cockpit and morning review.

> This page currently documents **launching** the Studio API. The rebuilt
> Studio UI and its full user guide land with the frontend PRs.

## Launch (WSL / Linux)

```
python3 -m makerbench.cli arena studio --port 8080
```

The server binds `127.0.0.1` by default. A non-loopback `--host` is refused
unless `--allow-remote` is passed, and even then a loopback bind keeps the
DNS-rebinding guard (only `127.0.0.1` / `localhost` Host headers are
accepted). Live provider-backed launches additionally require `--allow-live`;
without it every Studio launch is a zero-token `--stub` dry run.

## Windows/RDP launch

From a Windows desktop (including over RDP), `scripts\windows\start-arena-studio.ps1`
starts Arena Studio inside WSL and opens it in the default browser, without needing a
terminal open on both sides of the WSL boundary:

```
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts\windows\start-arena-studio.ps1
```

It is self-locating the same way `run-nightly-cad-arena.ps1` is (derives the repo root
from its own location via `$PSScriptRoot` + `wslpath` unless `-RepoWsl` is passed
explicitly), waits for `/api/health` to answer before opening the browser tab, and is
**loopback-only by construction**: there is no `-Host` parameter and it never passes
`--allow-remote`, so Arena Studio started this way is unreachable from any other
machine on the network no matter how it's invoked. Pass `-Port <n>` to use a port other
than 8080, or `-NoBrowser` to start the server without opening a tab.

> **Unverified on a live Windows desktop.** This script was written and covered by
> path-lint/contract tests in a Linux sandbox with no `wsl.exe`-driven Windows browser
> to actually launch against. Verification is tracked in #743 (WINDOWS-GATED).
