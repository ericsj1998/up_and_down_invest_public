"""덤프 xz 전환 — 내용이 같고 읽는 쪽이 그대로 읽는다."""

from __future__ import annotations

import gzip
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dumpio", ROOT / "scripts/research/dumpio.py")
assert SPEC is not None and SPEC.loader is not None
dumpio = importlib.util.module_from_spec(SPEC)
sys.modules["dumpio"] = dumpio
SPEC.loader.exec_module(dumpio)


def _gz(tmp: Path, body: bytes) -> Path:
    path = tmp / "dump9.jsonl.gz"
    with gzip.open(path, "wb") as out:
        out.write(body)
    os.utime(path, (1_000_000, 2_000_000))
    return path


def test_xz_keeps_content_and_time_and_removes_gz(tmp_path: Path) -> None:
    body = b'{"a": 1}\n{"b": 2}\n' * 1000
    gz = _gz(tmp_path, body)
    dumpio.xz_one(gz)
    xz = tmp_path / "dump9.jsonl.xz"
    assert not gz.exists() and xz.exists()
    assert not (tmp_path / "dump9.jsonl.xz.tmp").exists()
    assert int(xz.stat().st_mtime) == 2_000_000
    with dumpio.open_dump(tmp_path / "dump9.jsonl") as fh:
        assert fh.read().encode() == body
    assert dumpio.dump_exists(tmp_path / "dump9.jsonl")


def test_failure_leaves_the_gz_alone(tmp_path: Path) -> None:
    gz = tmp_path / "dump8.jsonl.gz"
    gz.write_bytes(b"not gzip")
    with pytest.raises(RuntimeError):
        dumpio.xz_one(gz)
    assert gz.exists()
    assert not (tmp_path / "dump8.jsonl.xz.tmp").exists()
    assert not (tmp_path / "dump8.jsonl.xz").exists()


def test_missing_dump_still_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        dumpio.open_dump(tmp_path / "dump7.jsonl")
