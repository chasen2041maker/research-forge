"""Fail CI when a high-confidence credential or tracked local environment file is present."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (
    REPOSITORY_ROOT / ".github",
    REPOSITORY_ROOT / "backend" / "research_forge",
    REPOSITORY_ROOT / "deploy" / "research-forge",
    REPOSITORY_ROOT / "docs",
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "README.zh-CN.md",
)
SECRET_PATTERNS = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "Anthropic API key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|DSA) PRIVATE KEY-----"),
}


def main() -> int:
    violations = _tracked_environment_file() + _secret_matches()
    if violations:
        print("Research Forge secret scan failed:", file=sys.stderr)
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("Research Forge secret scan passed.")
    return 0


def _tracked_environment_file() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return [".env must not be tracked; use .env.example or deployment placeholders."] if result.returncode == 0 else []


def _secret_matches() -> list[str]:
    violations: list[str] = []
    for root in SCAN_ROOTS:
        paths = root.rglob("*") if root.is_dir() else (root,)
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for name, pattern in SECRET_PATTERNS.items():
                for match in pattern.finditer(text):
                    line_number = text.count("\n", 0, match.start()) + 1
                    violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{line_number}: {name}")
    return violations


if __name__ == "__main__":
    raise SystemExit(main())
