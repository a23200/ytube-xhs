#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.schemas.models import FILE_KIND_TO_PATH  # noqa: E402
from app.services.report_writer import write_partial_asset_package  # noqa: E402
from app.services.runtime_store import store  # noqa: E402


def _register_existing_outputs(project_id: str) -> List[str]:
    paths = store.paths(project_id)
    registered = []
    for kind in FILE_KIND_TO_PATH:
        path = paths.file_for_kind(kind)
        if not path.exists():
            continue
        store.add_output(project_id, kind, path)
        registered.append(kind)
    return registered


def recover_stale_projects(older_than_seconds: int, dry_run: bool = False) -> Dict[str, Any]:
    recovered = store.mark_stale_running_failed(older_than_seconds=older_than_seconds, dry_run=dry_run)
    for item in recovered:
        if dry_run:
            item["registered_outputs"] = []
            continue
        project_id = item["project_id"]
        paths = store.paths(project_id)
        record = store.get(project_id)
        write_partial_asset_package(paths, item["error"], record.warnings)
        item["registered_outputs"] = _register_existing_outputs(project_id)
    return {
        "ok": True,
        "dry_run": dry_run,
        "older_than_seconds": older_than_seconds,
        "recovered_count": len(recovered),
        "projects": recovered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark stale running projects failed and preserve truthful partial packages.")
    parser.add_argument(
        "--older-than-seconds",
        type=int,
        default=3600,
        help="Recover running projects whose updated_at is older than this threshold. Default: 3600.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report stale running projects without mutating runtime files.")
    args = parser.parse_args()

    result = recover_stale_projects(args.older_than_seconds, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
