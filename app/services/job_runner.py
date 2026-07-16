import sys
from typing import Callable

from app.services import pipeline

TARGETS: dict[str, Callable[..., None]] = {
    "run_project_pipeline": pipeline.run_project_pipeline,
    "run_project_analysis_pipeline": pipeline.run_project_analysis_pipeline,
    "run_project_downstream_pipeline": pipeline.run_project_downstream_pipeline,
    "run_project_visual_pipeline": pipeline.run_project_visual_pipeline,
    "run_project_produce_pipeline": pipeline.run_project_produce_pipeline,
    "run_project_toutiao_produce_pipeline": pipeline.run_project_toutiao_produce_pipeline,
    "run_project_platform_produce_pipeline": pipeline.run_project_platform_produce_pipeline,
    "run_project_image_generation_pipeline": pipeline.run_project_image_generation_pipeline,
    "run_project_toutiao_image_generation_pipeline": pipeline.run_project_toutiao_image_generation_pipeline,
}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: python -m app.services.job_runner <target> <project_id> [args...]", file=sys.stderr)
        return 2
    target_name = argv[1]
    target = TARGETS.get(target_name)
    if target is None:
        print(f"unsupported target: {target_name}", file=sys.stderr)
        return 2
    target(argv[2], *argv[3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
