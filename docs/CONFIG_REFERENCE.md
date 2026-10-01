# Syke Config Reference

Authoritative reference for `~/.syke/config.toml` in the current runtime.

This document only covers the config model that actually exists in `syke/config_file.py` and `syke/config.py`.

---

## Precedence

Effective values resolve in this order:

1. Hardcoded defaults in `syke/config_file.py`
2. `~/.syke/config.toml`
3. Environment variables read by `syke/config.py` and provider runtime

Config is optional. Syke runs without a config file.

---

## What Exists

Current top-level config shape:

```toml
user = ""
timezone = "auto"

[synthesis]
[daemon]
[ask]
[paths]
```

What does not currently exist as typed config:

- `[sources]`
- `[distribution]`
- `[privacy]`

Those may return later, but they are not part of the current config contract.

---

## CLI

```bash
syke config init
syke config show
syke config path
```

---

## Top-Level Keys

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `user` | `string` | `""` | Default user ID; resolves to system username if empty | `SYKE_USER` |
| `timezone` | `string` | `"auto"` | Timezone mode for rendering/parsing | `SYKE_TIMEZONE` |

---

## `[synthesis]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `thinking_level` | `string` | `"medium"` | Pi thinking level for synthesis: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max` | `SYKE_SYNC_THINKING_LEVEL` |
| `timeout` | `int` | `600` | Wall-clock timeout in seconds | `SYKE_SYNC_TIMEOUT` |
| `first_run_timeout` | `int` | `1500` | Wall-clock timeout for the first synthesis run | `SYKE_SYNC_FIRST_RUN_TIMEOUT` |

---

## `[daemon]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `interval` | `int` | `900` | Loop interval in seconds | `SYKE_DAEMON_INTERVAL` |

---

## `[ask]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `timeout` | `int` | `600` | Ask timeout in seconds; durable config only, not caller env | none |
| `max_parallel` | `int` | `8` | Max concurrent daemon-owned temporary ask workers when the warm runtime is busy | `SYKE_MAX_PARALLEL_ASKS` |

---

## `[paths.distribution]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `skills_dirs` | `array[string]` | `.agents`, Pi, Claude, Antigravity CLI, Hermes, Codex, Cursor, OpenCode skill dirs | Capability installation targets |

There is no `data/{user}/` nesting. The current physical boundary is:

```text
~/.syke/
  workspace/   controller-writable graph, projections, and artifacts
  control/     writable runtime plus protected history and recovery
  bin/, pi/    installed runtime
  config.toml  installation configuration
```

Example:

```toml
[paths.distribution]
skills_dirs = [
    "~/.agents/skills",
    "~/.pi/agent/skills",
    "~/.claude/skills",
    "~/.gemini/antigravity-cli/skills",
    "~/.hermes/skills",
    "~/.codex/skills",
    "~/.cursor/skills",
    "~/.config/opencode/skills",
]
```

---

## Local Evidence Source Paths

Do not add `[paths.sources]` to `config.toml`. Local archive paths and activation
are managed together through `syke source add/list/remove` and persisted in
`~/.syke/source_selection.json`. ChatGPTExporter is currently the source that
supports explicit path registration; native harness roots remain unchanged.

Explicit paths replace that source's defaults, while changing source selection
preserves registered paths. v1 selection files remain supported; path registration
uses v2 with `source_paths` and preserves unrestricted (`null`) versus explicitly
empty (`[]`) selection. See [setup and archive registration](SETUP.md#adding-a-chatgptexporter-archive).

## Pi Agent State

Provider, model, auth, and endpoint state no longer live in `config.toml`.

Syke now keeps Pi-native runtime state in:

- `~/.syke/pi-agent/auth.json`
- `~/.syke/pi-agent/settings.json`
- `~/.syke/pi-agent/models.json`

Use the CLI to manage that state:

```bash
syke setup
syke auth
syke auth status
syke auth set openai --api-key KEY --model gpt-5.6-luna --use
syke auth login openai-codex --use
syke auth set localproxy --base-url URL --model MODEL --use
```

---

## Minimal Example

```toml
user = "your-name"
timezone = "auto"

[synthesis]
thinking_level = "medium"
timeout = 600
first_run_timeout = 1500

[daemon]
interval = 900

[ask]
timeout = 600
```

---

## Additional Environment Variables

These env vars are not config-file keys but are read by the runtime:

| Env Var | Default | Meaning |
|---|---|---|
| `SYKE_PROVIDER` | — | Per-process provider override |
| `SYKE_WORKSPACE_ROOT` | `~/.syke/workspace` | Override the controller-writable workspace |
| `SYKE_CONTROL_ROOT` | `~/.syke/control` | Override the host-owned control directory |
| `SYKE_PI_AGENT_DIR` | `~/.syke/pi-agent` | Override host-managed Pi agent state |

---

## Notes

- Unknown keys in typed sections are ignored with warnings.
- Provider/model/auth state does not live in `config.toml`. Use `syke auth` or `syke setup` for persisted Pi-native state, override per-process with `SYKE_PROVIDER`, or override per-command with `--provider`.
- Pi writes native ask and synthesis sessions directly to `control/sessions/`;
  Syke does not copy them. The host writes synthesis verdicts under
  `control/receipts/`; raw records live under `control/records/`.
- The synthesis recovery point/gate protects only the mutable graph. If Syke
  cannot create a cheap recovery point for a large graph DB, synthesis fails
  before handing control to Pi.
- `skills_dirs` is written as a normal TOML array.
- Removed path keys and `[rebuild]`, `[models]`, and `[providers]` sections from older configs are ignored.
