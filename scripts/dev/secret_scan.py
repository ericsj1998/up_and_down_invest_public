"""시크릿 스캔 — 추적 파일(기본)과 전체 git 이력(`--history`)에서 자격증명·개인키·주소를 찾는다.

퍼블릭 저장소 전환 점검(2026-09-06)에서 만들었다. 값은 **절대 그대로 찍지 않는다** — 앞 4자·길이만.

    uv run python scripts/dev/secret_scan.py             # 추적 파일만 (빠르다 · make secrets)
    uv run python scripts/dev/secret_scan.py --history   # 모든 브랜치·태그의 모든 커밋까지

종료 코드: 자격증명 계열(개인키 블록 · 클라이언트 시크릿 · API 키 · 토큰 · 비밀번호 값)이 하나라도
있으면 1. 주소·이메일 계열은 **경고**만 낸다 — 시크릿은 아니지만 공개 저장소에 둘지는 사람이 정한다.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# 이름만으로 의심스러운 파일 — 예시(`.example`)는 뺀다
_NAME_PAT = re.compile(
    r"(?i)(^|/)\.env(?!.*\.example$)|\.(pem|key|p12|pfx|ppk|jks|kdbx|gpg|asc)$"
    r"|id_rsa|id_ed25519|\.htpasswd$|\.netrc$"
)

# 자격증명 — 있으면 실패
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key_block": re.compile(
        r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----"
    ),
    "google_client_secret": re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}"),
    "nvidia_key": re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "openai_like": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "slack_token": re.compile(r"\bxox[abp]-[A-Za-z0-9\-]{10,}\b"),
    "assigned_secret": re.compile(
        r"(?i)\b[A-Z_]*(?:API_KEY|API_SECRET|SECRET_KEY|SESSION_SECRET|CLIENT_SECRET|PASSWORD|PASSWD"
        r"|TOKEN|PRIVATE_KEY)[A-Z_]*\s*[=:]\s*['\"]?([A-Za-z0-9+/_\-]{16,})['\"]?"
    ),
    "hex_key_near_word": re.compile(r"(?i)(?:key|secret)[^\n]{0,40}\b[a-f0-9]{32}\b"),
    # 로컬(localhost · 127.) 개발 DB 의 기본 비밀번호는 시크릿이 아니다 — 원격 호스트만 본다
    "db_url_with_password": re.compile(
        r"postgresql(?:\+psycopg)?://[^:\s]+:([^@\s<]{4,})@(?!localhost|127\.|postgres\b)"
    ),
    "redis_url_with_password": re.compile(
        r"redis://[^:\s]*:([^@\s<]{4,})@(?!localhost|127\.|redis\b)"
    ),
}
# 공개해도 유출은 아니지만 사람이 정할 것 — 경고
WARN_PATTERNS: dict[str, re.Pattern[str]] = {
    "gmail_address": re.compile(r"[A-Za-z0-9._%+\-]+@gmail\.com"),
    "ipv4_public_looking": re.compile(
        r"\b(?!10\.|127\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.|203\.0\.113\.)"
        r"\d{1,3}(?:\.\d{1,3}){3}\b"
    ),
    "ssh_user_at_host": re.compile(
        r"\b[a-z_][a-z0-9_\-]*@(?!127\.|localhost)\d{1,3}(?:\.\d{1,3}){3}\b"
    ),
}
# 알려진 무해 값 — 예시·CI 전용
ALLOW = (
    "ci_only_not_a_secret",
    "changeme",
    "example",
    "xxxxxxxx",
    "203.0.113.",
    # URL 경로는 시크릿이 아니다 — `TOKEN_… = "/admin/toss/warm"` 이 걸렸다
    # (2026-09-11 · 옛 커밋에 남음)
    "/admin/toss/",
)
_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]*$")  # 값이 아니라 변수 이름 (`KEY=NVIDIA_API_KEY`)


def _mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}***({len(value)})"


def _scan(text: str, source: str, hits: dict[str, dict[str, set[str]]]) -> None:
    if source.endswith("scripts/dev/secret_scan.py"):
        return  # 이 파일의 정규식이 자기 자신에 걸린다
    for name, pat in {**SECRET_PATTERNS, **WARN_PATTERNS}.items():
        for m in pat.finditer(text):
            value = m.group(1) if m.groups() and m.group(1) else m.group(0)
            if any(a in value for a in ALLOW):
                continue
            if value.startswith("$") or _IDENTIFIER.match(value):
                continue  # `${POSTGRES_PASSWORD}` 치환 · 변수 이름을 값으로 넣은 예시
            hits[name][source].add(_mask(value))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, errors="ignore", check=True
    ).stdout


def scan_tree(hits: dict[str, dict[str, set[str]]]) -> list[str]:
    """추적 중인 파일의 내용과 이름을 본다.

    Args:
        hits: 결과를 누적할 사전 (패턴 → 출처 → 마스킹된 값들).

    Returns:
        이름만으로 의심스러운 추적 파일 (`.env` · `.pem` · `id_rsa` …). 예시 파일은 뺀다.
    """
    files = _git("ls-files").split()
    suspicious = [f for f in files if _NAME_PAT.search(f)]
    for f in files:
        try:
            with Path(f).open(encoding="utf-8", errors="ignore") as fh:
                _scan(fh.read(), f"tree:{f}", hits)
        except OSError:
            continue
    return suspicious


def scan_history(hits: dict[str, dict[str, set[str]]]) -> list[str]:
    """모든 ref 의 모든 커밋에서 **추가된 줄**을 본다 (지워진 것도 이력에 남아 있다).

    Args:
        hits: 결과를 누적할 사전.

    Returns:
        이력에 한 번이라도 있던 의심 이름들.
    """
    names = _git("log", "--all", "--name-only", "--pretty=format:").split("\n")
    ever = sorted({n for n in names if n and _NAME_PAT.search(n)})
    log = _git("log", "--all", "-p", "--no-color", "--pretty=format:@@COMMIT %h")
    commit = path = "?"
    for line in log.split("\n"):
        if line.startswith("@@COMMIT "):
            commit = line[9:]
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            _scan(line[1:], f"hist:{commit}:{path}", hits)
    return ever


def main() -> int:
    """진입점.

    Returns:
        자격증명 계열 발견이 있으면 1, 없으면 0. 경고 계열은 종료 코드에 영향이 없다.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--history", action="store_true", help="전체 git 이력까지 (느리다)")
    args = parser.parse_args()

    hits: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    suspicious = scan_tree(hits)
    print(f"tracked-file names to look at: {suspicious or 'none'}")
    if args.history:
        ever = scan_history(hits)
        print(f"names ever in history to look at: {ever or 'none'}")

    failed = False
    for group, patterns in (("SECRET", SECRET_PATTERNS), ("WARN", WARN_PATTERNS)):
        for name in patterns:
            sources = hits.get(name)
            if not sources:
                continue
            if group == "SECRET":
                failed = True
            print(f"\n[{group}] {name} — {len(sources)} place(s)")
            for src in sorted(sources)[:15]:
                print(f"   {src}  {sorted(sources[src])[:3]}")
            if len(sources) > 15:
                print(f"   ... +{len(sources) - 15}")
    print("\nresult:", "🔴 secret-like values found" if failed else "✅ no credential-like values")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
