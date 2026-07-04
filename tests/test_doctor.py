from app.services import diagnostics
from scripts import doctor


def test_tesseract_version_uses_double_dash_flag(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, timeout, **kwargs):
        calls.append(command)

        class Result:
            stdout = "tesseract 5.5.2\n leptonica"
            stderr = ""

        return Result()

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)

    assert diagnostics._command_version("tesseract") == "tesseract 5.5.2"
    assert calls == [["tesseract", "--version"]]


def test_ffmpeg_version_uses_single_dash_flag(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, timeout, **kwargs):
        calls.append(command)

        class Result:
            stdout = "ffmpeg version 8.1.1"
            stderr = ""

        return Result()

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)

    assert diagnostics._command_version("ffmpeg") == "ffmpeg version 8.1.1"
    assert calls == [["ffmpeg", "-version"]]


def test_tesseract_language_status_reports_key_languages(monkeypatch):
    def fake_run(command, check, capture_output, text, timeout, **kwargs):
        assert command == ["/usr/bin/tesseract", "--list-langs"]

        class Result:
            returncode = 0
            stdout = "List of available languages in /tmp:\nchi_sim\neng\nosd\n"
            stderr = ""

        return Result()

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)

    status = diagnostics._tesseract_language_status("/usr/bin/tesseract")

    assert status["available"] is True
    assert status["languages"] == ["chi_sim", "eng", "osd"]
    assert status["key_languages"] == {"eng": True, "chi_sim": True, "chi_tra": False, "osd": True}
    assert status["error"] is None


def test_tesseract_language_status_reports_missing_command():
    status = diagnostics._tesseract_language_status(None)

    assert status["available"] is False
    assert status["languages"] == []
    assert status["key_languages"] == {}
    assert "not available" in status["error"]


def test_missing_requirements_for_partial_and_full_modes():
    diagnostics = {
        "ready_for": {
            "ingest": True,
            "subtitle_transcript": True,
            "frame_extraction": True,
            "whisper_transcript": False,
            "ocr": False,
            "llm_generation": False,
        }
    }

    assert doctor._missing_requirements(diagnostics, doctor.PARTIAL_REQUIREMENTS) == []
    assert doctor._missing_requirements(diagnostics, doctor.FULL_REQUIREMENTS) == [
        "whisper_transcript",
        "ocr",
        "llm_generation",
    ]


def test_doctor_main_returns_nonzero_when_required_capability_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "collect_diagnostics",
        lambda: {
            "ready_for": {
                "ingest": True,
                "subtitle_transcript": True,
                "frame_extraction": True,
                "whisper_transcript": True,
                "ocr": False,
                "llm_generation": True,
            }
        },
    )
    monkeypatch.setattr("sys.argv", ["doctor.py", "--require-full"])

    exit_code = doctor.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert '"ok": false' in output
    assert '"ocr"' in output
