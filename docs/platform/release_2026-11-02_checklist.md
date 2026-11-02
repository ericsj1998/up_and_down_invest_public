# 1.42.0 배포 점검표 — 2026-11-02 20:00 KST (실계좌 · 진짜 돈)

> 한 파일이 한 번에 하는 일 하나: **v1.41.0(10-16) 뒤 dev 에 쌓인 것 전부를 실계좌 서버에 올리고, 그 직후 사용자가 화면에서 옛 펀드를 보관한다.**
> 절차의 근거는 [ops_runbook.md](ops_runbook.md) §1 · §3 · [deploy.md](deploy.md) §12 · 변경 내용은 `CHANGELOG.md` 1.42.0 절(비공개) · 새 출발은 T607 문서(비공개).
> 🔴 배포 · 서버 env · `main` 합치기는 **사용자가 말할 때** 한다. 이 표는 준비자가 2026-11-02 오후에 로컬 점검만으로 채웠다 — 서버는 건드리지 않았다.
> 시각은 전부 **KST** (UTC 는 괄호). 칸은 `[ ]` 로 두고 돌리는 사람이 채운다.

## 0. 한 장 요약

| 항목 | 값 |
|---|---|
| 버전 | **1.42.0** (MINOR · 1.41.0 → 1.42.0 · `pyproject.toml` · `web/package.json` · `uv.lock`) — 매매법 선언 개정 · 화면(중지 토글) · 운영 기능(합친 청산가 문 · 펀드 전환 원자성)이 더해졌고 옛 판 · 펀드 파일 · 원장은 그대로 이어받는다(MAJOR 아님). PATCH 가 아닌 이유: 도는 펀드의 **매매 동작이 바뀐다**(아래 §1-5) |
| 마이그레이션 | **0135 하나** (`wf_trades.contract_adjust` 열 추가 · NULL 허용 · downgrade 있음). `bluegreen.sh` 1b 가 `alembic upgrade head` 를 먼저 돌린다. 🔴 **`SWAP=stop_first` 길은 마이그레이션을 안 돈다**(새 슬롯 `--no-deps`) → 그 길이면 `bash scripts/ops/remote.sh scripts/ops/migrate_live.sh` **먼저** |
| 로컬 점검(06:25 ~ 06:31Z) | `ruff check src tests` ✅ · `pytest -q`(전체 · -x 없이 · 06:34 ~ 06:40Z): **5,587 passed · 95 skipped(DB · Redis 없음) · 1 failed** — `tests/test_trade_evidence.py::TestJournal::test_written_and_read_back`(세션 직렬화 시험 스텁에 `blocked_recent` 없음 · T562 뒤 10-27 부터 알려진 선행 실패 · 배포 코드와 무관 · 고치지 않았다). `uv lock` ✅ 1.42.0. 공개 내보내기 스캔: 점검표 단독 0 ✅ · 🔴 dev HEAD 내보낸 트리 전체는 **119 건(파일 65 · 시험 51 · 전부 선행 · `scripts/ops/probe_*.py` · `config/live_review_expectations.yml` · `live_review/conditions.py` 등)** — 이번 변경과 무관 · 배포엔 영향 없음 · 별도 정리 태스크 |
| 서버 | Lightsail 1 GB · 2 vCPU 버스트(바뀌지 않았다 · T310 미결). 컨테이너 `updown_live-{api|api_b}` 중 하나가 리더 |
| 배포 길 | 기본 **블루그린**(`bash scripts/deploy/ship.sh` · 마이그레이션 포함 · 리더 끊김 없음). 메모리로 롤백하면 → 포지션 0 확인 뒤 **stop-first**(§2-3) |
| 봉 마감 | 20:00 KST = **11:00Z = 1H 마감 정각**. 4H 마감(KST) = 01 · 05 · 09 · **13 · 17 · 21**. 가장 가까운 4H 마감은 **21:00 KST(12:00Z) · 1 시간 뒤**. `ship.sh` 는 교체(4 단계)를 매시 **:05 ~ :45 UTC 에만** 시작한다 → 20:00 에 띄우면 빌드 · 전송(1.5 ~ 3 분) 뒤 **20:05 ~ 20:45 KST 안에 교체**. 20:45 를 넘기면 스크립트가 **21:05 KST 까지 기다린다**(4H 마감 뒤 · 그것도 정상). 코인 일봉 마감 09:00 KST · 나스닥 일봉 마감 06:00 KST(뉴욕 16:00 · 11-01 부터 EST) — 무관 |
| 새 출발(T607) | 배포 **뒤** 사용자가 화면에서 **1 · 2 단계만**(옛 펀드 보관 · 펀드 밖 판 접기). 새 펀드(3 단계)는 **나중에**. 스크립트(`fresh_start_t607.py`)는 오늘 쓰지 않는다 |

## 1. 배포 전 (로컬 · 연구 PC · 사용자 허락 전에도 할 수 있는 것)

- [x] 버전 1.42.0 — `pyproject.toml` · `web/package.json` · `uv.lock`(`uv lock` · updown 1.41.0 → 1.42.0) (준비자 · 2026-11-02).
- [x] `CHANGELOG.md` 1.42.0 절 — "미배포(dev · 2026-10-28)" 절 흡수 · 0135 · stop-first 는 `migrate_live.sh` 따로 표기.
- [x] `uv run ruff check src tests` ✅ (06:25Z).
- [x] `uv run pytest -q -p no:cacheprovider`(env 없이 · 전체) — 5,587 passed · 1 failed(선행 실패 · 위) · 95 skipped (첫 -x 실행은 5,119 에서 같은 시험으로 멈췄다). ⚠️ `.env.dev` 를 로드하고 돌리면 토스 프록시 시험 둘이 더 실패한다(알려진 것 · env 없이 돈다).
- [ ] `uv run python scripts/build/engine_gap.py --strict` — ship.sh 0 단계가 다시 돈다 · 🔴 `uv: command not found` 로 "엔진 갭 🔴" 가 **잘못** 보이면 PATH 문제다(`SKIP_GAP_CHECK=1` 로 넘기지 않는다 · 릴리스 스크립트 첫 줄이 PATH 를 넣는다).
- [x] 공개 저장소 규약 — 점검표(이 파일 · `docs/platform/` 은 공개)엔 매매법 이름 · 값 · 서버 주소 · 비밀이 없다(`strategy_scan.py --root` 단독 0 ✅ · 06:41Z). `CHANGELOG.md` 는 `strategy_split.yml private_paths` 안(비공개). 커밋 뒤 공개 클론 `git show --stat HEAD` 로 확인(post-commit 훅이 공개본에 올린다).
- [ ] **커밋 · push(dev)** — 🔴 연구 판(T755 `run_sweep.sh`)이 돌고 있다: pre-commit 이 몇 초 동안 작업 트리를 HEAD 로 되돌리므로 **판 사이에** 커밋한다(판 코드 변경은 없다 · 로그 둘만 수정 상태). `git add -f` 경로만.
- [ ] **dev → main**(사용자 허락 뒤): `git fetch origin && git merge origin/main`(GitHub 에서 직접 고친 커밋이 있으면) → `git push origin dev:main`. 그다음 ship 워크트리 `~/projects/updown_ship` 에서 `git checkout -- uv.lock && git pull --ff-only`(`uv run` 이 uv.lock 을 건드려 ff 가 막히는 함정).
- [ ] **Docker Desktop** 떠 있음 — WSL 에서 `docker info` 가 "Server Version" 을 찍는다(안내문만 찍고 0 을 돌려주면 연동 끊김 · ship.sh 가 거절).
- [ ] 릴리스 스크립트 `logs/t279/release_1420.sh`(준비됨 · 기본 블루그린) — 실행은 **PowerShell 도구 `run_in_background` 로 `wsl.exe -e bash <절대 경로>`**(인자에 `-c "…"` 를 넣으면 PowerShell 5.1 이 쪼개 아무것도 안 돈다). 로그 `logs/t279/release_1420.log`.

## 2. 배포 전 (서버 · 읽기만 · `remote.sh` · 19:30 ~ 19:55 KST)

- [ ] `make live-check` → `logs/live_review/check.md` 0 절부터: 리더 1 · 자가 점검 ⚠️ 유무 · **포지션 0 · 대기 주문 0 · 손절(조건부) 0** · 펀드 1 · 열린 판 39 · 펀드 밖 판 8(10-28 스냅샷 기준 — 바뀌었으면 적는다).
- [ ] `bash scripts/ops/remote.sh scripts/ops/probe_gate.py` — `POSITIONS` · `OPEN_ORDERS` · `STOP_ORDERS` 를 **적어 둔다**(배포 뒤 대조). T607 길은 0 · 0 · 0 이 전제다. 0 이 아니면: 블루그린은 대기 지정가를 이어받는다(`live_pending_restored` · T218)지만 **T607 1 · 2 단계는 미룬다**(판 접기 = 시장가 청산).
- [ ] **전 판 중지** — 화면 RUN 탭 "전부 중지"(슈퍼관리자) 또는 `bash scripts/ops/remote.sh scripts/ops/set_live_entries_halted.sh`(DB 키 · 새로 뜨는 판이 읽는다). 확인: live-check "전 판 정지 스위치" 1. 되살아난 판은 전부 신규 진입 꺼짐으로 뜬다 → 교체 창에 걸린 봉을 잃어도 진입은 어차피 없다.
- [ ] `bash scripts/ops/remote.sh scripts/ops/probe_supersede.sh` — 🔴 **펀드 매매법 전환이 도는 중이면 배포하지 않는다**(프로세스를 끄면 되돌림 보호가 끊긴다 · T564 두 번째 리뷰). 지금은 잔고가 1 계약 문에 못 미쳐 "미룸" 이어야 정상.
- [ ] `bash scripts/ops/remote.sh scripts/ops/status.sh` — 컨테이너 healthy · 30 분 오류 0 · 부하 · **메모리 · 스왑**(`free -m`) · 스틸 — 스왑이 700 MB 이상 차 있으면 블루그린이 메모리로 롤백할 확률이 크다 → §2-3.
- [ ] 장중 큰 움직임 아님(이미지 전송 600 MB gunzip 이 1 GB 서버 CPU 를 잠깐 다 쓴다).

### 2-3. 1 GB 서버 — 블루그린 대 stop-first 결정표

| 상황 | 길 | 마이그레이션 |
|---|---|---|
| 기본 | `bash scripts/deploy/ship.sh`(블루그린 · 리더 끊김 없음 · 이어받기) | `bluegreen.sh` 1b 가 돌린다 ✅ |
| 블루그린이 unhealthy · 메모리로 롤백(`probe_bluegreen_fail.sh` · `trader_lock_lost` 주고받기) **그리고 포지션 · 대기 · 손절 0** | `SWAP=stop_first bash scripts/deploy/ship.sh`(옛 슬롯 먼저 · 멈춤 약 3 ~ 4 분 · 1.41.0 이 이 길이었다) | 🔴 **안 돈다** — 1b 가 이미 돌았으면 끝(DB 가 head) · 아니면 `bash scripts/ops/remote.sh scripts/ops/migrate_live.sh` 먼저 |
| 포지션이 있다 | stop-first 금지(내려가 있는 몇 분 동안 거래소 조건부 주문만 손절을 지킨다) → 블루그린 재시도 또는 사용자에게 알린다 | — |

## 3. 배포 (20:00 KST · 사용자 지시 뒤)

```
20:00  wsl.exe -e bash /home/ericsj1998/projects/up_and_down_invest/logs/t279/release_1420.sh   (PowerShell run_in_background)
       0) 작업 트리 깨끗 · docker info · 엔진 갭 · 0b) sync_env_keys(FRED · Finnhub · 서버에 없을 때만 · 값 안 찍음)
       1) 빌드 · 2) compose · scripts/deploy 동기화 · 3) 이미지 전송(nice 19)
       4) :05 ~ :45 UTC 창 대기 → IMAGE_TAG=v1.42.0 → bluegreen.sh: 1b 마이그레이션(0135) → 새 슬롯 팔로워 healthy → 옛 슬롯 stop → 승격 → RUN 입양
       5) git tag v1.42.0 push · 6) https /api/health 200 · 7) 이미지 정리는 건너뜀(PRUNE=1 아님)
```

- [ ] 시작 시각 · 교체 시작 시각(로그 "=== 4)") · 새 리더 슬롯(api / api_b) · "✅ v1.42.0 배포 완료" 줄 · `EXIT 0`.
- [ ] 1b 마이그레이션 줄에 `0134_playbook_decls -> 0135_trade_contract_adjust` 가 있다. 🔴 실패(exit ≠ 0)면 새 슬롯을 올리지 않고 옛 슬롯이 그대로다 — 원인부터(`/tmp/bluegreen_migrate.log` 는 지워지므로 `migrate_live.sh` 로 다시 본다).
- [ ] 교체가 21:00 KST(12:00Z 4H 마감)에 걸리지 않았다(ship.sh 창이 막지만 **확인**). 걸렸으면 `scripts/ops/probe_bar_entries.sh <UTC 창>` 으로 교체 창 사건을 본다(진입은 중지 상태라 놓친 것은 없다).
- [ ] ship.sh 가 4 단계에서 멈췄으면 5 · 6 단계는 손으로: `git tag v1.42.0 <배포 커밋> && git push origin v1.42.0` · `curl https://<도메인>/api/health`.

## 4. 배포 뒤 (첫 30 분 · 20:10 ~ 20:45 KST)

- [ ] `bash scripts/ops/remote.sh scripts/ops/post_deploy_check.sh` — 리더 사건 셋 · redis 락 · `/health` `trading_leader: true` 한 슬롯만. `probe_leader_after_swap.sh` 로 옛 슬롯 내려감 · `trader_lock_lost` 주고받기 없음.
- [ ] `docker ps` 이미지 태그 `…/app:v1.42.0` · `…/web:v1.42.0` · 로컬 `git tag` 에 v1.42.0 · GitHub 에 태그 있음.
- [ ] `probe_gate.py` 다시 — §2 와 같은 포지션 · 대기 · 손절 수.
- [ ] **T564 재묶임 확인** — api 로그에 `fund_members_released`(펀드 밖 8 종이 옛 펀드에 다시 묶임) · 1 계약 문에 걸리는 멤버는 `fund_member_skipped`. `bash scripts/ops/remote.sh scripts/ops/probe_fund_members.sh` 로 묶인 판 = 열린 판. 🔴 그 뒤 **자동 대체(`superseded_by`)가 600 초 뒤 다시 시도**된다 — 잔고가 모자라면 `fund_playbook_switch_deferred`(사건) · 경고 로그 `fund_supersede_deferred`(미룸 · 상태 안 바꿈)가 정상 · `fund_playbook_supersede_failed` 나 반쪽 상태(awaiting_fund > 0)가 보이면 멈추고 본다.
- [ ] **T606 첫 걸음** — 닫힌 멤버 매매마다 `live_contract_adjust_booked` 1 회(정상 · 한 번만) · `fund_anchored` 가 다시 돈다(10-16 뒤 `fund_anchor_skipped` 로 멈춰 있던 것 — 펀드 밖 판이 8 → 0 이 되면 앵커 조건 "유일한 소유자" 가 다시 참).
- [ ] **T741 눈금** — `make live-check` 1 절 "합친 청산가 문(7일)" 줄이 있다(0 건 = 다몫 사이클 없음 · 참고). 로그 `live_merged_liq_check` 는 **둘째 몫 · 불타기가 들 때만** 찍히므로 배포 직후엔 없는 것이 정상 — 첫 다몫 사건 때 `live_merged_liq_check` 가 `live_add_filled` 보다 **앞에** 있는지 본다. `liq_inside_stop` · `stop_near_liquidation` 이 **새 진입**에서 뜨면 버그(기존 포지션이면 문이 보지 않은 상태).
- [ ] **T754 새 버튼** — 라이브 첫 화면(콘솔) 맨 위 배지: 지금은 전 판 중지 상태라 **붉은 "신규 진입 중지됨 — 든 포지션은 계속 관리됨 · <누가> · <시각 KST> · 도는 판 N"** 이 보여야 한다. 슈퍼관리자면 오른쪽에 "매매 재개" 버튼. RUN 탭 "전부 재개 / 전부 중지" 와 같은 스위치(누르면 배지가 바로 바뀐다). `GET /api/walkforward/live-all/auto` 200.
- [ ] `status.sh` — 브라우저 트래픽 5 분 non-2xx 0(499 폭주 없음) · 데모 저장본(`__demo`) 응답 · 메모리 · 스왑 · 스틸. 🔴 화면 문제는 nginx 접근 로그부터.
- [ ] `make live-check` 0 절 ⚠️ 를 전부 읽고 적는다(배포 전과 비교).

## 5. 새 출발 — 화면 길 (T607 · 배포 뒤 · 사용자가 누른다 · 1 · 2 단계만)

전제: §4 가 초록 · 포지션 0 · 대기 0 · 손절 0 · 전 판 중지 · **`probe_supersede.sh` 에 도는 전환 없음**(펀드 접기는 펀드 락을 기다린다 · 전환 중이면 끝날 때까지 멈춘 듯 보인다).

```
1  구글 재인증  https://<도메인>/api/auth/login?force=1   (실계좌 DELETE 는 재인증 1 시간 안 · 아니면 401)
2  콘솔(/console) 펀드 카드 머리 "삭제" → 안내 → "정말 삭제"   = DELETE /rebalancer/{id}
   → 펀드 정의가 logs/funds/archive/<id>.json 으로(dropped_at) · 소유한 멤버 판(배포 뒤 재묶임이 됐으면 39 · 안 됐으면 31)이 함께 접힘
3  RUN 표에 남은 GATE 판 → 줄마다 "삭제" → "정말 삭제"   = DELETE /walkforward/sessions/{key}   (한 번에 여러 개 없음 · 분당 30 회 상한 안)
   🔴 T564 재묶임이 됐으면 2 에서 전부 접혀 남는 판이 0 일 수 있다 — 그러면 3 은 건너뛴다. 남은 판의 종목 · 수를 먼저 적는다
4  make live-check — 1 절 열린 판 0 · 펀드 선언 0 · awaiting_fund 0 · fund_restore_failed 0 · 화면 RUN 목록 비어 있음("죽은 RUN" 도 0)
5  (나중 · 다른 날) 콘솔 펀드 만들기 — 선언은 선택창 최신 1개 · 멤버 40 · 시작 자본 = 그때 지갑 총액 → 첫 틱 앵커 account → live-check · 그다음 "매매 재개"
```

- 되돌리기: 2 는 `logs/funds/archive/<id>.json` 을 `logs/funds/` 로 되돌리고(`dropped_at` 지움) api 재기동(옛 장부가 돌아오고 판은 새 키로 뜬다 — "새 출발" 뜻이 사라지므로 실수했을 때만). 3 은 판 단위로 **되돌릴 수 없다**(저널 삭제 · `wf_runs` 닫힘 · 포지션 0 에서만 하므로 돈은 안 움직인다).
- 서버 자원: 펀드 접기는 세션 39 개를 거두므로 수십 초 걸릴 수 있다(화면 20 초 타임아웃이 와도 서버는 shield 로 끝까지 간다 · RUN 표를 새로 고쳐 본다).

## 6. 되돌리기 (이미지 · 서버에서)

```
bash scripts/ops/remote.sh -- 'cd ~/updown && sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=v1.41.0/" .env.live && ENV=live IMAGE_TAG=v1.41.0 bash scripts/deploy/bluegreen.sh'
```

- 이미지는 서버에 남아 있다(정리하지 않았다 · PRUNE 없음). 블루그린이 메모리로 안 되면 포지션 0 확인 뒤 `swap_stop_first.sh`(IMAGE_TAG 가 .env.live 값).
- **0135 는 그대로 둔다** — 1.41.0 코드는 `contract_adjust` 열을 읽지 않으므로 무해하다(downgrade 는 있지만 돌리지 않는다 · 되돌린 뒤 다시 올릴 때 더하기만이라 안전).
- ⚠️ 되돌리면 매매법 선언(`legs_revision`)도 옛것으로 되살아나 다리 동작이 1.41.0 때로 돌아간다 · T564 원자성 없는 전환 코드로 돌아간다(`superseded_by` 가 다시 반쪽 전환을 낼 수 있다 → 되돌린 상태를 오래 두지 않는다).
- ⚠️ §5 에서 접은 펀드 · 판은 이미지 되돌림과 **무관**하다(§5 되돌리기 참조).
- 되돌린 뒤: `post_deploy_check.sh` · `probe_gate.py` · `make live-check`.

## 7. 🔴 함정 모음 (이번 배포에만 해당하는 것)

1. **배포 재기동만으로 도는 펀드의 다리 동작이 바뀐다** — 펀드를 되살릴 때 선언(`legs_revision`)에서 다리 규칙을 다시 읽는다. 사용자가 10-18 에 채택한 개정이며 의도된 것이지만, "코드만 올렸는데 매매가 달라졌다" 로 읽히지 않게 적어 둔다. 자세한 것은 `CHANGELOG.md` 1.42.0 절(비공개).
2. **stop-first 는 마이그레이션을 안 돈다** — 이번 배포엔 0135 가 있다. 그 길이면 `migrate_live.sh` 먼저(§2-3).
3. **재기동 뒤 자동 대체가 다시 시도된다**(600 초 뒤 · 매시) — 잔고가 1 계약 문에 못 미치면 "미룸" 이 정상. §5 펀드 접기와 겹치지 않게 `probe_supersede.sh` 를 먼저 본다.
4. **T564 재묶임으로 "펀드 밖 판 8" 이 0 이 될 수 있다** — §5-3 의 8 회 삭제가 필요 없어질 수 있다. 수를 세고 누른다.
5. **20:00 KST 는 1H 마감 정각** — ship.sh 가 교체를 :05 까지 미룬다(정상 동작 · "멈췄나" 가 아니다). 20:45 를 넘기면 21:05 까지 기다리는 것도 정상.
6. **연구 판이 돌고 있다**(T755 sweep) — 커밋은 판 사이에. 배포 빌드(`docker build`)와 판이 CPU · 메모리를 나눠 쓴다 — 빌드가 느릴 수 있다(로컬 메모리 12 GB 가용 · 06:25Z).
7. 블루그린 뒤 **web 재생성**은 `bluegreen.sh` 4b · 5 가 한다(nginx upstream 캐시 → 502 함정은 거기서 막힌다). 수동으로 api 만 재생성했다면 `docker restart updown_live-web-1`.
8. **`.env.live` 값은 건드리지 않는다** — ship.sh 가 바꾸는 것은 `IMAGE_TAG` 한 줄 + 허용 목록 자료 출처 키(없을 때만). 새 env 이름 없음(이번 변경에 새 설정 키는 `config/risk.yml` 안 · env 아님).
9. 알려진 시험 실패 1(`test_trade_evidence` 저널 스텁)은 배포 코드와 무관하다 — 고치지 않고 적어 둔다(별도 태스크).

## DoD

- [ ] 태그 `v1.42.0` · 새 리더 healthy · `/api/health` 200 · `make live-check` 0 절 ⚠️ 를 읽고 적음 · T564 재묶임 로그 · 배지 보임 · §5 1 · 2 단계 끝(사용자) · 이 표의 칸이 채워짐.
