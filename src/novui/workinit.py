"""Novel project initialization."""

from pathlib import Path
import re
import shutil

from novui.config import Settings
from novui.gitinspect import run_git
from novui.schema import validate_or_raise
from novui.workrepo import commit_all
from novui.works import WorkInfo, get_work, register_work
from novui.yamlio import write_yaml_atomic

TEMPLATE_DIR: Path = Path(__file__).resolve().parents[2] / "templates" / "novel" / "v1"

_WORK_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def init_work(settings: Settings, path: Path, work_key: str, title: str) -> WorkInfo:
    """Initialize a new novel project from template and register it.

    Args:
        settings: Controller settings.
        path: Path to the new project repository directory.
        work_key: Unique identifier matching ^[a-z0-9][a-z0-9-]{0,63}$.
        title: Title of the novel work.

    Returns:
        WorkInfo for the initialized and registered work.

    Raises:
        FileExistsError: If path already exists.
        FileNotFoundError: If parent directory of path does not exist.
        ValueError: If work_key pattern invalid, title empty, or work_key already registered.
    """
    if path.exists():
        raise FileExistsError(f"Target path already exists: {path}")

    if not path.parent.exists():
        raise FileNotFoundError(f"Parent directory of path does not exist: {path.parent}")

    if not _WORK_KEY_RE.match(work_key):
        raise ValueError(f"Invalid work_key {work_key!r}; must match pattern ^[a-z0-9][a-z0-9-]{{0,63}}$")

    if not title or not title.strip():
        raise ValueError("title cannot be empty")

    try:
        get_work(settings, work_key)
        already_registered = True
    except KeyError:
        already_registered = False

    if already_registered:
        raise ValueError(f"Work {work_key!r} is already registered in registry")

    # 2. Create path and git init -b main
    path.mkdir(parents=True)
    abs_path = path.resolve(strict=False)
    run_git(abs_path, "init", "-b", "main")

    # 3. Copy template contents to path
    if not TEMPLATE_DIR.is_dir():
        raise RuntimeError(f"Template directory not found at {TEMPLATE_DIR}")

    for item in TEMPLATE_DIR.iterdir():
        dest = abs_path / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # 4. Write updated project.yaml and validate with schema 'project'
    project_doc = {
        "format_version": 1,
        "work_key": work_key,
        "title": title,
    }
    validate_or_raise(project_doc, "project")
    write_yaml_atomic(abs_path / "project.yaml", project_doc)

    # 5. Commit all with NovUI-Edit: human-content trailer
    commit_all(
        abs_path,
        f"init work {work_key}",
        [("NovUI-Edit", "human-content")],
        name=settings.git_name,
        email=settings.git_email,
    )

    # 6. Register work in registry
    return register_work(settings, work_key, abs_path, 1)
