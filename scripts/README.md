# scripts — 용도별로 갈라 둔다

> 애플리케이션 코드는 `src/updown/` 에 있다. 여기는 그것을 **부르는** 도구다. 컨테이너에 들어가는 것은 `runtime/` 과 `deploy/` 둘뿐이고
> (`.dockerignore` 화이트리스트), 나머지는 개발 PC 에서 돈다.

| 폴더 | 무엇 | 예 |
|---|---|---|
| `runtime/` | **컨테이너 안에서** 도는 것 — 이미지에 복사된다 | `seed_instruments.py` · `backfill_cli.py` · `measure_costs.py` · `_progress.py`(진행률 기록기) |
| `deploy/` | 배포 절차 — 서버 호스트에서 | `ship.sh`(한 줄 배포) · `bluegreen.sh` · `init_db.sh` · `verify_clean_db.py` |
| `ops/` | 서버 점검 — 시크릿을 찍지 않는다. 서버 주소는 `host.env`(비추적 · `host.env.example` 복사) | `remote.sh <스크립트>` · `status.sh` · `fund.sh` · `probe_gate.py` · `env_sync.sh` |
| `dev/` | 개발 편의 | `dev.sh`(vite) · `check.sh` · `check_md_links.py`(문서 링크 래칫) · `docstring_audit.py`(docstring 절 누락 0 강제) · `secret_scan.py`(시크릿 스캔) · `progress_report.py` · `smoke_*.py` |
| `data/` | 수집 · 백필 · 델타 · 적재 검증 | `backfill_*.sh` · `collect_gate_delta.py` · `aggregate_cli.py` · `verify_against_api.py` |
| `build/` | 산출물 빌드 | `evidence_bundle.py` — `docs/measurements` → `web/public/evidence.json` (원문 인용 검사) |
| `research/` | 매매법 탐침 · 격자 · 감사 — 결론이 실전으로 갈 때는 `src/` 로 옮겨 적는다 | `gate_backtest.py` · `trend_grid.py` · `backtest_lab.py` · `scalp/`(1m 랩) · `scenarios/`(합성 45미래) · `live_audit/` · `discovery/` |
| `history/` | **동결된 측정 기록** — 재포맷하지 않는다 (`pyproject` 면제) | `2026-08-30_full_report/` … |

## 서로 import 하는 법

폴더가 갈린 뒤에도 연구 스크립트들은 형제를 그대로 import 한다(`import gate_backtest`). 옮긴 스크립트 머리에 있는 네 줄이 그 길을 연다.

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
```

`sys.path.insert` 줄은 ruff 의 E402 면제라 그 아래 import 에 `noqa` 가 필요 없다. 새 스크립트를 `research/` 에 두면 형제 import 는 그냥 된다.

## 규칙

- 실행은 저장소 루트에서 `uv run python scripts/<폴더>/<이름>.py`. 상대 경로(`logs/` · `docs/measurements/`)는 루트 기준이다.
- 30분 넘게 도는 것은 `_progress.py` 기록기를 붙여 `make progress` 로 진행률·남은 시간을 보이게 한다.
- 시크릿은 어디에도 찍지 않는다. `ops/` 프로브는 이름·유무·요약만 출력한다.
