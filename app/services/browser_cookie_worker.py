import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from app.services.cookie_manager import CookieManagerError, export_browser_cookie_file, sanitize_error_text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--browser", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = export_browser_cookie_file(
            args.platform,
            args.browser,
            args.profile,
            Path(args.output),
        )
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except CookieManagerError as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False))
        return 2
    except Exception as exc:
        error = CookieManagerError(
            "cookie_browser_import_failed",
            "The isolated browser Cookie reader failed unexpectedly.",
            {"error": sanitize_error_text(exc)},
        )
        print(json.dumps({"ok": False, "error": error.to_dict()}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
