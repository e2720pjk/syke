"""Install adapter markdown guides into the workspace."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from syke.observe.catalog import active_sources
from syke.observe.seeds import get_seed_adapter_md_path

logger = logging.getLogger(__name__)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# SHA-256 of previously shipped seed adapter markdowns.
#
# ensure_adapters() refuses to overwrite a user-customized adapter, but it must
# still be able to upgrade an untouched older seed in place. Hashes are added
# here (append-only) whenever a seed ships; never remove an old entry.
KNOWN_SEED_HASHES: dict[str, tuple[str, ...]] = {
    # adapter-opencode.md pre-2.0 (single-schema legacy-only version)
    "opencode": (
        "9180c5508e6b5cca8aca7b1ab9750fe9062f8a98b3cb04f258291b6bf5804497",
    ),
}


@dataclass(frozen=True)
class BootstrapResult:
    source: str
    status: Literal["installed", "existing", "upgraded", "customized", "skipped"]
    detail: str


def customized_adapter_hints(
    workspace_root: Path,
    *,
    selected_sources: tuple[str, ...] | None = None,
) -> list[BootstrapResult]:
    """Return customized deployed adapters without changing the workspace.

    This read-only inspection is used by setup/doctor to explain manual repair
    while ``ensure_adapters`` remains responsible for installation/upgrades.
    """
    adapters_dir = workspace_root / "adapters"
    selected_set = set(selected_sources) if selected_sources is not None else None
    results: list[BootstrapResult] = []
    for spec in active_sources():
        if selected_set is not None and spec.source not in selected_set:
            continue
        md_src = get_seed_adapter_md_path(spec.source)
        if md_src is None:
            continue
        target_md = adapters_dir / f"{spec.source}.md"
        if not target_md.is_file():
            continue
        seed_text = md_src.read_text(encoding="utf-8")
        existing_text = target_md.read_text(encoding="utf-8")
        if existing_text == seed_text:
            continue
        if _sha256_text(existing_text) in KNOWN_SEED_HASHES.get(spec.source, ()):
            continue
        results.append(
            BootstrapResult(
                spec.source,
                "customized",
                f"{target_md} (user-modified; review manually or delete and re-run `syke connect`)",
            )
        )
    return results


def ensure_adapters(
    workspace_root: Path,
    *,
    selected_sources: tuple[str, ...] | None = None,
) -> list[BootstrapResult]:
    """Install adapter markdowns into workspace/adapters/."""
    adapters_dir = workspace_root / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)

    results: list[BootstrapResult] = []
    selected_set = set(selected_sources) if selected_sources is not None else None

    for spec in active_sources():
        if selected_set is not None and spec.source not in selected_set:
            continue
        md_src = get_seed_adapter_md_path(spec.source)
        if md_src is None:
            results.append(BootstrapResult(spec.source, "skipped", "no adapter markdown seed"))
            continue

        target_md = adapters_dir / f"{spec.source}.md"

        seed_text = md_src.read_text(encoding="utf-8")

        if target_md.exists():
            existing_text = target_md.read_text(encoding="utf-8")
            if existing_text == seed_text:
                results.append(BootstrapResult(spec.source, "existing", str(target_md)))
                continue
            known_hashes = KNOWN_SEED_HASHES.get(spec.source, ())
            if _sha256_text(existing_text) in known_hashes:
                # Untouched older seed: safe to upgrade in place.
                target_md.write_text(seed_text, encoding="utf-8")
                logger.info("Upgraded adapter markdown for %s", spec.source)
                results.append(BootstrapResult(spec.source, "upgraded", str(target_md)))
            else:
                # User-customized adapter: never overwrite. Doctor/setup hint
                # lives in the detail string so CLI surfaces can show it.
                detail = (
                    f"{target_md} (user-modified; not overwritten. To adopt the new "
                    f"seed, review changes manually or delete the file and re-run "
                    f"`syke connect`)"
                )
                logger.warning("Preserved customized adapter markdown for %s", spec.source)
                results.append(BootstrapResult(spec.source, "customized", detail))
        else:
            target_md.write_text(seed_text, encoding="utf-8")
            logger.info("Installed adapter markdown for %s", spec.source)
            results.append(BootstrapResult(spec.source, "installed", str(target_md)))

    return results
