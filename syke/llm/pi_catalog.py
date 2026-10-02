"""Pi provider catalog, model binding, OAuth, and connectivity probes."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass

from syke.config_file import THINKING_LEVELS
from syke.llm import pi_install as _pi_install
from syke.pi_state import build_pi_agent_env, get_default_model, load_pi_auth
from syke.runtime.child_env import build_child_process_env

logger = logging.getLogger("syke.llm.pi_client")

_PI_THINKING_LEVELS = frozenset(THINKING_LEVELS)


@dataclass(frozen=True)
class PiLaunchBinding:
    provider: str | None
    model: str


@dataclass(frozen=True)
class PiProviderCatalogEntry:
    id: str
    models: tuple[str, ...]
    available_models: tuple[str, ...]
    default_model: str | None
    oauth: bool
    oauth_name: str | None = None
    requires_base_url: bool = False


def _get_active_provider_spec():
    try:
        from syke.llm.env import resolve_provider

        return resolve_provider()
    except Exception:
        return None


def _raw_pi_model_request(model_override: str | None = None) -> tuple[str, bool]:
    if model_override:
        return model_override, True

    provider = _get_active_provider_spec()
    provider_name = _pi_provider_name(provider)

    default_model = get_default_model()
    if default_model:
        return default_model, True

    if provider_name:
        provider_default = _load_pi_provider_default_model(provider_name)
        if provider_default:
            return provider_default, False
    raise RuntimeError(
        "No Pi model is configured. Set Pi defaultModel or choose a provider/model in `syke setup`."
    )


def _pi_provider_name(provider) -> str | None:
    if provider is None:
        return None
    provider_id = getattr(provider, "id", None)
    return provider_id if isinstance(provider_id, str) and provider_id else None


def _looks_like_pi_alias(model_id: str) -> bool:
    if model_id.endswith("-latest"):
        return True
    return not bool(re.search(r"-\d{8}$", model_id))


def _split_thinking_suffix(pattern: str) -> tuple[str, str | None]:
    last_colon = pattern.rfind(":")
    if last_colon == -1:
        return pattern, None
    suffix = pattern[last_colon + 1 :]
    if suffix in _PI_THINKING_LEVELS:
        return pattern[:last_colon], suffix
    return pattern, None


def _match_pi_model_pattern(
    provider_name: str, requested: str, model_ids: tuple[str, ...]
) -> str | None:
    lower_to_id = {model_id.lower(): model_id for model_id in model_ids}
    candidate = requested.strip()

    exact = lower_to_id.get(candidate.lower())
    if exact:
        return exact

    provider_prefix = f"{provider_name}/"
    if candidate.lower().startswith(provider_prefix.lower()):
        stripped = candidate[len(provider_prefix) :].strip()
        exact = lower_to_id.get(stripped.lower())
        if exact:
            return exact
        candidate = stripped

    base_candidate, thinking = _split_thinking_suffix(candidate)
    exact = lower_to_id.get(base_candidate.lower())
    if exact:
        return f"{exact}:{thinking}" if thinking else exact

    matches = [model_id for model_id in model_ids if base_candidate.lower() in model_id.lower()]
    if not matches:
        return None

    aliases = sorted(model_id for model_id in matches if _looks_like_pi_alias(model_id))
    resolved = aliases[-1] if aliases else sorted(matches)[-1]
    return f"{resolved}:{thinking}" if thinking else resolved


def _format_model_examples(model_ids: tuple[str, ...]) -> str:
    examples = sorted(model_ids)[:3]
    return ", ".join(repr(model_id) for model_id in examples)


def _run_pi_node_script(script: str) -> subprocess.CompletedProcess[str]:
    node_bin = _pi_install.ensure_node_binary()
    env = _build_subprocess_env(build_pi_agent_env())
    return subprocess.run(
        [str(node_bin), "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(_pi_install.active_pi_prefix()),
        env=env,
    )


def _prepare_host_oauth_for_runtime(provider: str | None) -> None:
    """Refresh stored OAuth in the trusted host before the sandbox starts."""
    if not provider:
        return
    credential = load_pi_auth().get(provider)
    if not isinstance(credential, dict) or credential.get("type") != "oauth":
        return

    launcher = _pi_install.ensure_pi_binary()
    result = subprocess.run(
        [launcher, "auth", "check", "--provider", provider, "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_pi_install.active_pi_prefix()),
        env=_build_subprocess_env(build_pi_agent_env(), provider=provider),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Host OAuth preparation failed for {provider!r}: {detail[:500]}")


def _load_pi_catalog() -> tuple[PiProviderCatalogEntry, ...]:
    if not _pi_install._package_path(
        _pi_install.active_pi_prefix(), _pi_install.PI_PACKAGE
    ).exists():
        return ()

    script = """
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { defaultModelPerProvider } from
  "./node_modules/@earendil-works/pi-coding-agent/dist/core/model-resolver.js";

const runtime = await ModelRuntime.create({
  allowModelNetwork: false,
  refreshOnCreate: false,
});
const allModels = runtime.getModels();
const availableModels = await runtime.getAvailable();
const providersById = new Map(runtime.getProviders().map((provider) => [provider.id, provider]));
const availableByProvider = new Map();
for (const model of availableModels) {
  const current = availableByProvider.get(model.provider) ?? [];
  current.push(model.id);
  availableByProvider.set(model.provider, current);
}
const grouped = new Map();
for (const model of allModels) {
  const current = grouped.get(model.provider) ?? [];
  current.push(model.id);
  grouped.set(model.provider, current);
}
const payload = Array.from(grouped.entries())
  .sort((a, b) => a[0].localeCompare(b[0]))
  .map(([provider, modelIds]) => {
    const ids = [...new Set(modelIds)].sort();
    const providerModels = allModels.filter((model) => model.provider === provider);
    const preferred = defaultModelPerProvider[provider];
    const defaultModel = preferred && ids.includes(preferred) ? preferred : (ids[0] ?? null);
    const oauth = providersById.get(provider)?.auth?.oauth;
    return {
      id: provider,
      models: ids,
      availableModels: [...new Set(availableByProvider.get(provider) ?? [])].sort(),
      defaultModel,
      oauth: Boolean(oauth),
      oauthName: oauth?.name ?? null,
      requiresBaseUrl: providerModels.some((model) => !String(model.baseUrl ?? "").trim())
    };
  });
process.stdout.write(JSON.stringify(payload));
"""
    try:
        result = _run_pi_node_script(script)
    except Exception:
        return ()

    if result.returncode != 0:
        logger.debug("Failed to query Pi catalog: %s", result.stderr.strip())
        return ()

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ()

    if not isinstance(raw, list):
        return ()

    entries: list[PiProviderCatalogEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        provider_id = item.get("id")
        models = item.get("models")
        available = item.get("availableModels")
        if (
            not isinstance(provider_id, str)
            or not isinstance(models, list)
            or not isinstance(available, list)
        ):
            continue
        entries.append(
            PiProviderCatalogEntry(
                id=provider_id,
                models=tuple(model for model in models if isinstance(model, str) and model),
                available_models=tuple(
                    model for model in available if isinstance(model, str) and model
                ),
                default_model=item.get("defaultModel")
                if isinstance(item.get("defaultModel"), str)
                else None,
                oauth=bool(item.get("oauth")),
                oauth_name=item.get("oauthName")
                if isinstance(item.get("oauthName"), str)
                else None,
                requires_base_url=bool(item.get("requiresBaseUrl")),
            )
        )
    return tuple(entries)


def get_pi_provider_catalog() -> tuple[PiProviderCatalogEntry, ...]:
    return _load_pi_catalog()


def run_pi_oauth_login(provider_id: str, *, method: str = "auto") -> None:
    """Run Pi's native OAuth login flow with its standard browser handoff."""
    if method not in {"auto", "browser", "device-code"}:
        raise ValueError(f"Unsupported Pi login method: {method}")

    script = """
import readline from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { openBrowser } from
  "./node_modules/@earendil-works/pi-coding-agent/dist/utils/open-browser.js";

const provider = process.env.SYKE_PI_LOGIN_PROVIDER;
const requestedMethod = process.env.SYKE_PI_LOGIN_METHOD ?? "auto";
if (!provider) {
  throw new Error("Missing SYKE_PI_LOGIN_PROVIDER");
}

const normalizeMethod = (value) => String(value).replaceAll("-", "_");
const rl = readline.createInterface({ input: stdin, output: stdout });

try {
  const runtime = await ModelRuntime.create({
    allowModelNetwork: false,
    refreshOnCreate: false,
  });
  const interaction = {
    notify: (event) => {
      if (event.type === "auth_url") {
        console.log(`Open this URL to continue: ${event.url}`);
        if (event.instructions) console.log(event.instructions);
        if (requestedMethod !== "device-code") openBrowser(event.url);
      } else if (event.type === "device_code") {
        console.log(`Open ${event.verificationUri} and enter code ${event.userCode}`);
      } else if (event.type === "info" || event.type === "progress") {
        console.log(event.message);
        for (const link of event.links ?? []) console.log(`${link.label ?? "Open"}: ${link.url}`);
      }
    },
    prompt: async (prompt) => {
      if (prompt.type === "manual_code" && prompt.signal) {
        return await new Promise((resolve) => {
          const finish = () => resolve("");
          if (prompt.signal.aborted) finish();
          else prompt.signal.addEventListener("abort", finish, { once: true });
        });
      }
      if (prompt.type === "select") {
        if (prompt.options.length === 0) throw new Error("Pi offered no login methods");
        if (requestedMethod === "auto") return prompt.options[0].id;
        const requested = normalizeMethod(requestedMethod);
        const selected = prompt.options.find((option) => normalizeMethod(option.id) === requested);
        if (!selected) {
          const supported = prompt.options.map((option) => option.id).join(", ");
          throw new Error(
            `Pi does not offer ${requestedMethod} login here (supported: ${supported})`,
          );
        }
        return selected.id;
      }
      const placeholder = prompt.placeholder ? ` (${prompt.placeholder})` : "";
      const options = prompt.signal ? { signal: prompt.signal } : undefined;
      return await rl.question(`${prompt.message}${placeholder}: `, options);
    },
  };
  await runtime.login(provider, "oauth", interaction);
} finally {
  rl.close();
}
"""
    result = subprocess.run(
        [str(_pi_install.ensure_node_binary()), "--input-type=module", "-e", script],
        text=True,
        cwd=str(_pi_install.active_pi_prefix()),
        env=_build_subprocess_env(
            build_pi_agent_env(
                {
                    "SYKE_PI_LOGIN_PROVIDER": provider_id,
                    "SYKE_PI_LOGIN_METHOD": method,
                }
            ),
            provider=provider_id,
        ),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Pi login failed for {provider_id!r}")


def probe_pi_provider_connection(
    provider_id: str,
    model_id: str,
    *,
    timeout_seconds: int = 45,
    prompt: str = "Reply with only: ping",
) -> tuple[bool, str]:
    """Run a minimal non-tool Pi request to verify provider connectivity."""
    try:
        result = subprocess.run(
            [
                str(_pi_install.ensure_pi_binary()),
                "--provider",
                provider_id,
                "--model",
                model_id,
                "--no-tools",
                "-p",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(_pi_install.active_pi_prefix()),
            env=_build_pi_process_env(provider=provider_id),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"probe timed out after {timeout_seconds}s"
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode == 0 and stdout:
        return True, stdout
    detail = stderr or stdout or f"exit {result.returncode}"
    return False, detail[:500]


def _load_pi_provider_default_model(provider_name: str) -> str | None:
    for entry in _load_pi_catalog():
        if entry.id == provider_name:
            return entry.default_model
    return None


def _load_pi_provider_model_ids(provider_name: str) -> tuple[str, ...]:
    for entry in _load_pi_catalog():
        if entry.id == provider_name:
            return entry.models
    return ()


def _resolve_pi_launch_binding_for_request(
    provider, requested_model: str, explicit_model: bool
) -> PiLaunchBinding:
    provider_name = _pi_provider_name(provider)

    if provider_name is None:
        return PiLaunchBinding(provider=None, model=requested_model)

    known_model_ids = _load_pi_provider_model_ids(provider_name)
    if not known_model_ids:
        return PiLaunchBinding(provider=provider_name, model=requested_model)

    resolved_model = _match_pi_model_pattern(provider_name, requested_model, known_model_ids)
    if resolved_model:
        return PiLaunchBinding(provider=provider_name, model=resolved_model)

    if explicit_model:
        return PiLaunchBinding(provider=provider_name, model=requested_model)

    example_text = _format_model_examples(known_model_ids)
    provider_id = getattr(provider, "id", provider_name)
    raise RuntimeError(
        f"Configured synthesis model {requested_model!r} is not a known Pi model for provider "
        f"{provider_name!r}. Set Pi defaultModel for {provider_id!r} to an exact Pi model ID"
        f" like {example_text}."
    )


def resolve_pi_launch_binding(model_override: str | None = None) -> PiLaunchBinding:
    provider = _get_active_provider_spec()
    requested_model, explicit_model = _raw_pi_model_request(model_override)
    return _resolve_pi_launch_binding_for_request(provider, requested_model, explicit_model)


def resolve_pi_model(model_override: str | None = None) -> str:
    """Resolve the Pi model from override -> config -> exact provider-scoped model."""
    return resolve_pi_launch_binding(model_override).model


def _build_subprocess_env(
    runtime_env: dict[str, str],
    *,
    provider: str | None = None,
) -> dict[str, str]:
    """Build a bounded child env for Pi instead of inheriting the full host shell."""
    return build_child_process_env(runtime_env, provider=provider)


def _build_pi_process_env(
    runtime_env: dict[str, str] | None = None,
    *,
    provider: str | None = None,
) -> dict[str, str]:
    """Build the exact Pi child-process env used by both probe and runtime launch."""
    resolved_provider = provider or _pi_provider_name(_get_active_provider_spec())
    return _build_subprocess_env(
        runtime_env or build_pi_agent_env(),
        provider=resolved_provider,
    )
