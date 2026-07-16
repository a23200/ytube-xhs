import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

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
        assert filename.lstrip("*").endswith(archive.name)
    finally:
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)
