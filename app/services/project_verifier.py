from pathlib import Path
from typing import Any, Dict

from scripts.verify_project import verify_project


def verify_runtime_project(project_dir: Path) -> Dict[str, Any]:
    return verify_project(project_dir)
