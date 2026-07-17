import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_macos_deploy_shell_scripts_parse():
    scripts = [
        "install-from-github-macos.sh",
        "update-macos.sh",
        "start.sh",
        "deploy/macos/install_macos.sh",
        "deploy/macos/manage.sh",
        "deploy/macos/healthcheck.sh",
        "deploy/macos/bootcheck.sh",
        "scripts/package_macos_deploy.sh",
    ]
    for script in scripts:
        result = subprocess.run(["bash", "-n", str(ROOT / script)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_macos_bootcheck_plist_template_contains_launchd_keys():
    template = (ROOT / "deploy/macos/com.ytube-xhs.bootcheck.plist.template").read_text(encoding="utf-8")
    assert "com.ytube-xhs.bootcheck" in template
    assert "RunAtLoad" in template
    assert "StartInterval" in template
    assert "bootcheck.out.log" in template


@pytest.mark.parametrize("extra_args", [[], ["--no-whisper", "--skip-brew"]])
def test_fixed_macos_updater_runs_with_optional_passthrough_on_bash_3_semantics(tmp_path, extra_args):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    downloaded_installer = tmp_path / "downloaded-installer.sh"
    args_file = tmp_path / "installer-args.txt"
    env_file = tmp_path / "installer-env.txt"
    downloaded_installer.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
: > "${YTXHS_TEST_ARGS_FILE:?}"
for argument in "$@"; do
  printf '%s\\n' "$argument" >> "$YTXHS_TEST_ARGS_FILE"
done
printf '%s|%s|%s|%s\\n' "$YTXHS_REPO" "$YTXHS_REF" "$YTXHS_APP_DIR" "$YTXHS_PORT" > "${YTXHS_TEST_ENV_FILE:?}"
""",
        encoding="utf-8",
    )
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="${2:?missing curl output}"; shift 2 ;;
    *) shift ;;
  esac
done
cp "${YTXHS_TEST_INSTALLER:?}" "${output:?missing curl output}"
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "YTXHS_REPO": "owner/repo",
        "YTXHS_REF": "test-ref",
        "YTXHS_APP_DIR": str(tmp_path / "app"),
        "YTXHS_PORT": "9123",
        "YTXHS_POST_UPDATE_ACTION": "none",
        "YTXHS_TEST_INSTALLER": str(downloaded_installer),
        "YTXHS_TEST_ARGS_FILE": str(args_file),
        "YTXHS_TEST_ENV_FILE": str(env_file),
    }

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "update-macos.sh"), *extra_args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert args_file.read_text(encoding="utf-8").splitlines() == extra_args
    assert env_file.read_text(encoding="utf-8").strip() == f"owner/repo|test-ref|{tmp_path / 'app'}|9123"
    assert "Update complete." in result.stdout


def test_macos_deploy_package_excludes_local_state_and_secrets(tmp_path):
    dist_dir = ROOT / "dist"
    before = set(dist_dir.glob("ytube-xhs-macmini-*.tar.gz")) if dist_dir.exists() else set()
    env = {**os.environ, "TMPDIR": str(tmp_path)}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/package_macos_deploy.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    archives = set(dist_dir.glob("ytube-xhs-macmini-*.tar.gz")) - before
    assert len(archives) == 1
    archive = archives.pop()
    checksum = archive.with_suffix("").with_suffix(".sha256")
    try:
        with tarfile.open(archive, "r:gz") as package:
            names = package.getnames()
        relative_names = [name.split("/", 1)[1] if "/" in name else "" for name in names]
        forbidden_parts = {
            ".git",
            ".venv",
            "runtime",
            "dist",
            "output",
            ".playwright-cli",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
        }
        assert all(not (set(Path(name).parts) & forbidden_parts) for name in relative_names)
        assert all(Path(name).name != ".env" for name in relative_names)
        assert all(not name.endswith(".pyc") for name in relative_names)
        assert any(name.endswith("/app/main.py") for name in names)
        assert any(name.endswith("/deploy/macos/install_macos.sh") for name in names)
        assert any(name.endswith("/PACKAGE-MANIFEST.txt") for name in names)
        digest, filename = checksum.read_text(encoding="utf-8").strip().split(maxsplit=1)
        assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
        assert filename.lstrip("*") == archive.name
        assert not Path(filename.lstrip("*")).is_absolute()
    finally:
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)
