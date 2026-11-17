"""Run the real external EPUBCheck with bounded time and Java memory."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .epub import EpubError


def epubcheck(epub: Path, jar: Path, output: Path, stem: str, timeout: int = 45) -> dict:
    if not jar.is_file():
        raise EpubError(f"EPUBCheck JAR not found: {jar}")
    if not 1 <= timeout <= 60:
        raise EpubError("EPUBCheck timeout must be 1–60 seconds")
    report = output / f"{stem}-epubcheck.json"
    log = output / f"{stem}-epubcheck.log"
    input_name = epub.name if epub.resolve().parent == output.resolve() else str(epub.resolve())
    command = ["java", "-Xmx256m", "-jar", str(jar.resolve()), input_name,
               "--json", report.name, "--quiet"]
    result = {"status": "error", "command": command, "report": report.name, "log": log.name}
    try:
        with log.open("wb") as stream:
            process = subprocess.run(command, cwd=output, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        result["exit_code"] = process.returncode
        if report.is_file():
            data = json.loads(report.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("checker"), dict):
                raise ValueError("EPUBCheck JSON has no checker object")
            checker = data.get("checker", {})
            if any(type(checker.get(n)) is not int or checker[n] < 0 for n in ("nFatal", "nError", "nWarning")):
                raise ValueError("EPUBCheck JSON is missing valid message counts")
            result["version"] = checker.get("checkerVersion")
            result["counts"] = {name: checker.get(name) for name in ("nFatal", "nError", "nWarning", "nUsage")}
            result["status"] = "passed" if process.returncode == 0 and checker.get("nFatal") == 0 and checker.get("nError") == 0 else "failed"
        else:
            result["error"] = "EPUBCheck did not produce its JSON report; inspect the log"
        result["note"] = "Format conformance only; accessibility and reading behavior were not assessed."
    except subprocess.TimeoutExpired:
        result.update({"status": "timed_out", "exit_code": None, "timeout_seconds": timeout})
    except (OSError, ValueError) as exc:
        result.update({"status": "error", "exit_code": None, "error": str(exc)})
    return result
