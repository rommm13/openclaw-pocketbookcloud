#!/usr/bin/env python3
"""Offline public-tree gate. Diagnostics never echo secret material."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.parse import urlsplit

ROOT_FILES = {
    "SKILL.md", "README.md", "README.en.md", "ARCHITECTURE.md",
    "ARCHITECTURE.en.md", "SECURITY.md", "SECURITY.en.md",
    "PUBLIC_RELEASE.md", "CHANGELOG.md", "LICENSE", ".gitignore",
    ".clawhubignore", ".github/workflows/ci.yml", "tools/release_check.py",
}
VENDOR_PUBLIC_FINGERPRINTS = {
    "a60fefc41a03f3145f61ec2b47cbcb8d49c3204ebd634e1c9704530d4dd6a286",
    "70b56914187347c48501bba23e4d986c3c0d5603cc452fbd937c577f096236b6",
}
SENSITIVE_KEYS = {
    "access_token", "refresh_token", "client_secret", "client_id",
    "public_client_secret", "public_client_id", "password", "authorization",
}
SYNTHETIC_VALUES = {
    "", "x", "y", "bad", "secret", "supersecret",
    "access-token", "refresh-token", "fixture-access-token", "fixture-refresh-token",
    "test-client-id", "test-client-secret", "test-password",
    "Bearer secret", "Bearer token",
}
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
ASSIGNMENT = re.compile(
    r'''["']?\b(access_token|refresh_token|client_secret|client_id|public_client_secret|public_client_id|password|authorization)\b["']?\s*[:=]\s*["']([^"'\r\n]*)["']''',
    re.I,
)
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]+")
SIGNED_URL = re.compile(
    r"https://[^\s\"'<>]+[?&](?:token|signature|access_token|X-Amz-Signature)=[^\s\"'<>]+",
    re.I,
)


def allowed_path(name: str) -> bool:
    if name in ROOT_FILES:
        return True
    return bool(re.fullmatch(
        r"scripts/pocketbook_lib/[a-z_]+\.py|"
        r"references/[a-z_-]+\.md|"
        r"tests/test_[a-z_]+\.py|"
        r"tests/fixtures/[a-z_]+\.json|"
        r"scripts/pocketbook\.py",
        name,
    ))


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_credential_literal(key: str, value: str) -> bool:
    if value in SYNTHETIC_VALUES or value.startswith("REPLACE_WITH_"):
        return True
    if key.casefold() in {"client_id", "client_secret", "public_client_id", "public_client_secret"}:
        return _fingerprint(value) in VENDOR_PUBLIC_FINGERPRINTS
    return False


def content_issues(data: bytes) -> set[str]:
    issues: set[str] = set()
    if PRIVATE_KEY.search(data):
        issues.add("private key")
    text = data.decode("utf-8", errors="replace")
    if JWT.search(text):
        issues.add("JWT-like token")
    for match in SIGNED_URL.finditer(text):
        host = (urlsplit(match.group(0)).hostname or "").casefold()
        # Reserved example domains are used by offline negative tests only.
        if host.endswith((".example", ".invalid", ".test")) or host in {"example.com", "www.example.com"}:
            continue
        issues.add("signed URL")

    for match in ASSIGNMENT.finditer(text):
        key, value = match.group(1), match.group(2)
        if not safe_credential_literal(key, value):
            issues.add("credential-like literal")

    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        value = None

    def visit(obj):
        if isinstance(obj, dict):
            if {"expires_at", "provider_alias", "shop_id"} <= set(obj) or "account_fingerprint" in obj:
                issues.add("PocketBook runtime state")
            for key, val in obj.items():
                if key.casefold() in SENSITIVE_KEYS:
                    if not isinstance(val, str) or not safe_credential_literal(key, val):
                        issues.add("credential-like JSON")
                visit(val)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(value)
    return issues


def check(root: Path) -> list[str]:
    tracked_result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=True,
    )
    tracked = set(tracked_result.stdout.decode().split("\0")) - {""}
    seen: set[str] = set()
    problems: list[str] = []

    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        if base == root:
            dirs[:] = [d for d in dirs if d != ".git"]
        for name in list(dirs):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                problems.append("symlink directory rejected")
                dirs.remove(name)
            elif name == "__pycache__" and not any(p.startswith(relative + "/") for p in tracked):
                dirs.remove(name)

        for name in files:
            path = base / name
            relative = path.relative_to(root).as_posix()
            seen.add(relative)
            if relative.startswith(".git/"):
                continue
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                problems.append("non-regular file rejected")
                continue
            if not allowed_path(relative):
                problems.append("unexpected file")
            if path.stat().st_size > 2 * 1024 * 1024:
                problems.append("oversized release file")
                continue
            problems.extend(sorted(content_issues(path.read_bytes())))

    if tracked - seen:
        problems.append("tracked files missing from checked tree")
    return problems


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        problems = check(root)
    except (OSError, subprocess.SubprocessError):
        print("Release check FAILED: repository inspection failed.", file=sys.stderr)
        return 1
    if problems:
        print("Release check FAILED: " + "; ".join(sorted(set(problems))), file=sys.stderr)
        return 1
    print("Release check passed: public tree contains only permitted files and no detected private credentials/runtime state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
