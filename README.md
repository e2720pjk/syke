# Syke

[![PyPI](https://img.shields.io/pypi/v/syke)](https://pypi.org/project/syke/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![CI](https://github.com/saxenauts/syke/actions/workflows/ci.yml/badge.svg)](https://github.com/saxenauts/syke/actions/workflows/ci.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg)](LICENSE)

Syke is a local memory agent that works with your other AI agents.

![Syke workflow — local synthesis cycle, ask / record interfaces, distribution to harnesses](docs/syke.png)

It runs in the background as an ambient agent, keeps up with your work
across supported local harnesses and concurrent sessions, and serves a coherent
memory for other agents to rely on.

It reads your local agent activity and maintains a coherent timeline of objects,
intent, and progress in prose. It serves a projection as `MEMEX.md` plus a CLI
interface.

Your agents use `syke ask`, `syke record`, and `syke memex` in their workflow.
As a self-maintaining memory agent, Syke adapts by revising its own durable
memory state over time. It can also act as a sidekick for debugging,
brainstorming, and research alongside your primary coding agents.

Syke is deliberately experimental and incomplete. It does not try to reproduce
every feature in established memory products, and pre-1.0 releases may make
intentional compatibility breaks. The runtime is exercised across multiple
memory environments as ongoing research into self-learning systems.

## Install

```bash
pipx install syke
syke setup
```

Alternative:

```bash
uv tool install syke
syke setup
```

`syke setup` is interactive. It inspects your machine for active harnesses and
uses Pi agent core for auth and runtime.

### Install with an agent

Give a terminal agent this repository URL and say:

> Install or upgrade Syke from this repository with `uv` using Python 3.12+.
> Run `syke setup --agent` and follow its JSON `status` and `next_steps`.
> Setup is complete only after Syke's background service is running.
> If provider login is needed, present `provider_choices` and run the selected
> `auth_options` command; Pi opens the browser or shows a device code.

The installing agent does not need to be one of Syke's observed sources.

## First Run

The normal flow is simple:

```bash
syke setup
```

Setup walks through:

- provider/auth setup
- local harness detection
- source selection
- workspace initialization at `~/.syke/`
- background service setup
- macOS access checks for Desktop, Documents, and Downloads
- first memory synthesis

On macOS, setup performs those three checks through the installed background
Syke process. macOS may ask for access one folder at a time. Choose Allow for
the folders you want Syke to observe. Other normal folders under your home
directory do not need this step.

After setup, keep working. The first synthesis can take a few minutes depending
on how much local history Syke finds. The timeline explains what is happening
instead of leaving you with an empty screen.

Once memory starts landing:

```bash
syke memex
syke ask "what changed this week?"
```

## Daily Use

```bash
syke memex
syke ask "what should I remember about this project?"
syke record "Decision: keep the onboarding flow interactive and local."
syke status
syke doctor
```

For long notes, pasted transcripts, or shell-sensitive content, prefer stdin so
the shell does not reinterpret or retain the content:

```bash
printf '%s\n' 'Decision: keep $(literal) and `quoted` chars.' | syke record
```

The important split:

- `syke memex` shows the current memory projection.
- `syke ask` searches and reasons over the underlying timeline.
- `syke record` admits an external record; the next synthesis decides whether it changes memory.
- `syke web --open` shows the local visual timeline.

## Local Timeline

Syke serves a private local timeline. It is for visualization only. 

```bash
syke web --open
```

The timeline shows:

- memory cycles
- asks and traces
- MEMEX content and diffs
- linked memory cells
- first-run/bootstrap state
- daemon log tail

## Supported Harnesses

Syke reads local artifacts from agent tools you already use. This list controls
native history ingestion, not which terminal agents can install or use Syke:

- Claude Code
- Codex
- Pi coding agent
- OpenCode
- Cursor
- GitHub Copilot
- Google Antigravity (2.0, CLI, and IDE surfaces)
- Hermes
- ChatGPT Web / ChatGPTExporter (saved local archives)

Add an existing ChatGPTExporter archive without rerunning setup:

```bash
syke source add chatgpt-web ~/Downloads/ChatGPTExport-xxxx
syke source list chatgpt-web
```

Registration validates and enables the source; it does not ingest content or
refresh ChatGPT. Pi reads the existing indexes and full conversation files when
needed. See [archive registration](docs/SETUP.md#adding-a-chatgptexporter-archive)
for path, permission, and coverage rules, and [PLATFORMS.md](PLATFORMS.md)
for exact artifact paths and current status.

## How Agents Use Syke

Once setup is done, agents should usually use three commands:

```bash
syke memex
syke ask "what is the current context?"
syke record "Decision: ship the onboarding fix before changing the API."
```

For automation, `syke setup --agent` returns JSON with a `status`, exact
`next_steps`, and setup diagnostics. Its output is the setup protocol for
installers and non-interactive agents. Humans should start with plain
`syke setup`.

More detail: [Setup Guide](docs/SETUP.md).

## What Syke Stores

Syke is local-machine first.

- Installation root: `~/.syke/`
- Controller-writable workspace: `~/.syke/workspace/`
- Mutable graph: `~/.syke/workspace/syke.db`
- Current projection: `~/.syke/workspace/MEMEX.md`
- Installed self-model: packaged `syke/runtime/syke_self.md` (read-only)
- Adapter guides: `~/.syke/workspace/adapters/{source}.md`
- Durable operational runtime: `~/.syke/control/runtime/`
- Host-managed Pi auth/provider state: `~/.syke/pi-agent/`
- Protected native Pi sessions: `~/.syke/control/sessions/`
- Protected final synthesis receipts: `~/.syke/control/receipts/`
- Protected raw records: `~/.syke/control/records/`

On macOS, Pi writes its native sessions as trusted runtime state. The model's
`read`, `bash`, `edit`, and `write` tools run behind a filesystem sandbox:
the current user's `$HOME` is readable while ordinary computer files remain
read-only. Only the Syke workspace and durable runtime are writable. Sessions,
receipts, records, and recovery state remain explicitly non-writable.
`workspace/syke.db` is Syke's only active semantic/control database; native
tools may keep their own source stores. Linux does not yet provide the same
OS-enforced model-tool boundary. Content returned by model tools can be
processed by the configured provider and retained in protected native sessions.

## Docs

- [Setup Guide](docs/SETUP.md)
- [Providers](docs/PROVIDERS.md)
- [Config Reference](docs/CONFIG_REFERENCE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Docs Index](docs/README.md)
