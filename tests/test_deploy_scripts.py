import subprocess
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
