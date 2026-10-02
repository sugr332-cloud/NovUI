"""Model catalog management and resolution."""

from pathlib import Path
import re
import time
from typing import Any

from novui.checks import CheckResult
from novui.config import Settings
from novui.container import (
    build_agy_mounts,
    image_id,
    image_label,
    run_container,
)
from novui.jobhome import job_home
from novui.jobrecord import now_iso
from novui.procrun import ProcResult
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_model_catalog
from novui.yamlio import load_yaml, write_yaml_atomic

ROLES: frozenset[str] = frozenset({"draft", "range_edit", "chapter_rewrite"})

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ModelUnavailable(Exception):
    """Raised when a requested model is unavailable in catalog or not configured."""

    def __init__(self, message: str, *, role: str, model_id: str) -> None:
        super().__init__(message)
        self.role = role
        self.model_id = model_id


def parse_agy_models(text: str) -> list[dict[str, str]]:
    """Parse agy models command output into list of model dicts.

    Non-empty lines are split on first whitespace sequence into (id, label).
    Only lines whose id matches ^[A-Za-z0-9][A-Za-z0-9._-]*$ are retained.
    """
    models: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        mid = parts[0]
        label = parts[1].strip() if len(parts) > 1 else ""
        if _MODEL_ID_RE.match(mid):
            models.append({"id": mid, "label": label})
    return models


def fetch_agy_models(
    settings: Settings,
    *,
    container_runner: Any = run_container,
) -> dict[str, Any]:
    """Fetch available models from AGY container and build catalog dict."""
    timestamp_sec = int(time.time())
    job_id = f"job-{timestamp_sec}"
    container_name = f"novui-models-{timestamp_sec}"
    log_dir = settings.data_dir / "models" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    with job_home(settings.jobhome_root, job_id, settings.agy_token_path) as jh:
        mounts = build_agy_mounts(job_home=jh, jobhome_root=settings.jobhome_root)
        proc_res: ProcResult = container_runner(
            name=container_name,
            image=settings.agy_image,
            mounts=mounts,
            command=["agy", "models"],
            timeout_seconds=120.0,
            log_dir=log_dir,
        )

    if proc_res.exit_code != 0:
        raise RuntimeError(f"agy models exited with code {proc_res.exit_code}")

    stdout_text = proc_res.stdout.decode("utf-8", errors="replace")
    models = parse_agy_models(stdout_text)
    if not models:
        raise RuntimeError("No models found from agy models output")

    version = image_label(settings.agy_image, "org.novui.agy.version") or "unknown"
    try:
        img_id = image_id(settings.agy_image)
    except Exception:
        img_id = "unknown"

    catalog = {
        "fetched_at": now_iso(),
        "agy_image": settings.agy_image,
        "image_id": img_id,
        "agy_version": version,
        "models": models,
    }
    return catalog


def _catalog_path(settings: Settings) -> Path:
    return settings.data_dir / "models" / "agy.yaml"


def save_catalog(settings: Settings, catalog: dict[str, Any]) -> Path:
    """Validate and atomically save model catalog to <data_dir>/models/agy.yaml."""
    validate_or_raise(catalog, "model_catalog")
    sem_errs = check_model_catalog(catalog)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for model_catalog: {sem_errs}", errors=sem_errs)
    path = _catalog_path(settings)
    write_yaml_atomic(path, catalog)
    return path


def load_catalog(settings: Settings) -> dict[str, Any] | None:
    """Load and validate model catalog from <data_dir>/models/agy.yaml if exists."""
    path = _catalog_path(settings)
    if not path.is_file():
        return None
    data = load_yaml(path)
    validate_or_raise(data, "model_catalog")
    sem_errs = check_model_catalog(data)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for model_catalog: {sem_errs}", errors=sem_errs)
    return data


def new_model_ids(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    """Return model IDs in new catalog that are absent in old catalog."""
    if old is None:
        return [m["id"] for m in new.get("models", [])]
    old_ids = {m["id"] for m in old.get("models", [])}
    return [m["id"] for m in new.get("models", []) if m["id"] not in old_ids]


def _settings_path(settings: Settings) -> Path:
    return settings.data_dir / "settings.yaml"


def load_controller_settings(settings: Settings) -> dict[str, Any]:
    """Load controller settings from <data_dir>/settings.yaml or return empty structure."""
    path = _settings_path(settings)
    if not path.is_file():
        return {"models": {}, "works": {}}
    data = load_yaml(path)
    validate_or_raise(data, "controller_settings")
    return data


def select_model(
    settings: Settings,
    role: str,
    model_id: str,
    *,
    work_key: str | None = None,
) -> None:
    """Configure model_id for role globally or per work_key.

    Raises:
        ValueError: If role is not in ROLES.
        ModelUnavailable: If catalog is missing or model_id is not in catalog.
    """
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}, allowed: {sorted(ROLES)}")

    catalog = load_catalog(settings)
    if catalog is None:
        raise ModelUnavailable(
            f"Model {model_id!r} is unavailable: no catalog loaded",
            role=role,
            model_id=model_id,
        )

    available_ids = {m["id"] for m in catalog.get("models", [])}
    if model_id not in available_ids:
        raise ModelUnavailable(
            f"Model {model_id!r} is not in catalog",
            role=role,
            model_id=model_id,
        )

    cfg = load_controller_settings(settings)
    if work_key is not None:
        works = cfg.setdefault("works", {})
        work_cfg = works.setdefault(work_key, {"models": {}})
        work_cfg.setdefault("models", {})[role] = model_id
    else:
        cfg.setdefault("models", {})[role] = model_id

    validate_or_raise(cfg, "controller_settings")
    write_yaml_atomic(_settings_path(settings), cfg)


def resolve_model(
    settings: Settings,
    role: str,
    *,
    work_key: str | None = None,
) -> str:
    """Resolve active model ID for role.

    Prefers work_key setting over global setting.
    Raises ModelUnavailable if not configured or not present in latest catalog.
    """
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}, allowed: {sorted(ROLES)}")

    cfg = load_controller_settings(settings)
    model_id = None
    if work_key is not None:
        work_models = cfg.get("works", {}).get(work_key, {}).get("models", {})
        model_id = work_models.get(role)

    if model_id is None:
        model_id = cfg.get("models", {}).get(role)

    if model_id is None:
        raise ModelUnavailable(
            f"No model configured for role {role!r}",
            role=role,
            model_id="",
        )

    catalog = load_catalog(settings)
    if catalog is None:
        raise ModelUnavailable(
            f"Cannot verify model {model_id!r}: catalog missing",
            role=role,
            model_id=model_id,
        )

    available_ids = {m["id"] for m in catalog.get("models", [])}
    if model_id not in available_ids:
        raise ModelUnavailable(
            f"Configured model {model_id!r} for role {role!r} is not in current catalog",
            role=role,
            model_id=model_id,
        )

    return model_id
