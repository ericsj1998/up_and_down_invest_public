# 외부 공개 배포 절차 (2026-08-30)

> 이 문서는 **인증이 붙은 뒤**의 배포를 다룬다. 인증 자체의 설계는
> [live_trading_logic.md](../strategy/live_trading_logic.md) 가 아니라 코드가 근거다 —
> `common/security/roles.py` · `apps/api/auth.py`.

## 🔴 공개하기 전에 반드시 확인할 것

배포 전 실측(2026-08-30): API 에 인증 코드가 **0 줄**이었다. `web`(nginx)이 `/api/` 를
그대로 넘기므로 URL 을 아는 사람이 `POST /walkforward/live`(판 생성) ·
`/exchange/close-position`(청산) · `/rebalancer`(펀드 삭제)를 그대로 부를 수 있었다.

지금은 막힌다. **막혔는지 밖에서 확인하는 것**이 이 문서의 첫 항목이다.

```bash
# 쿠키 없이 불러 본다 — 401 이 나와야 한다
for p in /walkforward/sessions /exchange/state /auth/users; do
  printf '%-28s %s\n' "$p" \
    "$(curl -s -o /dev/null -w '%{http_code}' https://<도메인>/api$p)"
done
# /health 만 200 이고 나머지는 401 이면 정상
```

## 1. 구글 OAuth

| 항목 | 값 |
|---|---|
| 클라이언트 유형 | 웹 애플리케이션 |
| 승인된 리디렉션 URI | `https://<도메인>/api/auth/callback` |
| 승인된 JavaScript 원본 | **비워 둔다** (서버끼리 코드를 교환한다) |
| 게시 상태 | **테스트** 권장 — 등록된 사람만 로그인된다 |

⚠️ 리디렉션 URI 는 `.env` 의 `GOOGLE_REDIRECT_URI` 와 **글자까지 같아야** 한다.
끝의 슬래시 유무도 다르게 본다. 안 맞으면 구글이 `redirect_uri_mismatch` 로 거절하고,
그 오류는 우리 로그가 아니라 **구글 화면**에 뜬다.

## 2. `.env.live`

```bash
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://<도메인>/api/auth/callback
SESSION_SECRET=$(openssl rand -base64 48)
ADMIN_EMAILS=you@example.com

# TLS 앞단이 쓴다
PUBLIC_DOMAIN=<도메인>
ACME_EMAIL=you@example.com
```

🔴 **하나라도 비면 로그인이 아예 안 뜬다.** 반쪽 설정으로 로그인 화면만 뜨면 사람은
보호받는다고 믿는데 실제로는 아무도 안 막힌다 — 그 상태를 만들지 않는다 (규칙 #8).

값이 코드에 닿는지는 배포 전에 확인할 수 있다:

```bash
set -a; . ./.env.live; set +a
uv run python scripts/history/2026-08-30_full_report/t168_auth_env_check.py
```

### 2-1. 실계좌 주문 세 줄 (T157 · 2026-09-04)

```
GATE_API_KEY=...        # 선물 Read&Write 만 · Withdraw OFF · IP 화이트리스트 = Static IP
GATE_API_SECRET=...
LIVE_ORDERS=1           # 운영자 스위치. 0 이면 키가 있어도 테스트넷으로 간다
```

세 줄이 **전부** 있어야 실주문이다. 스위치가 1 인데 키가 없으면 어댑터를 얻는 순간 예외로 죽는다 —
"실거래인 줄 알았는데 페이크머니" 를 막기 위해 페이퍼로 떨어뜨리지 않는다. 실거래 관문 탭의
"OrderGateway LIVE 분기" 행이 이 셋을 읽어 **열림/차단**을 보여 준다.

### 2-2. 🔴 Gate 실계좌는 **단방향(one-way) 포지션 모드**여야 한다 (2026-09-05 실측)

새 Gate 계좌는 기본이 **양방향(dual)** 모드다. 그 모드에서는 `GET /futures/usdt/positions/{contract}` 와
레버리지 응답이 객체가 아니라 **리스트**로 와서(롱·숏 두 줄) 콘솔이 503, 판 시작이 20초 타임아웃으로 죽는다.
테스트넷은 단방향이라 코드가 그 모양만 안다. 포지션이 0 일 때 한 번만 바꾸면 된다 — API 로:

```
POST /futures/usdt/dual_mode?dual_mode=false     # 응답 in_dual_mode=false · position_mode=single
```

(첫 배포에서 서버 안에서 실행해 바꿨다.) 화면으로는 Gate 선물 → 설정 → 포지션 모드 → 단방향.
`in_dual_mode` 는 `GET /futures/usdt/accounts` 에 있다 — 콘솔 계정 카드가 언젠가 이 값을 보여 줘야 한다.

## 3. 띄우기

```bash
make up ENV=live PROXY=1
```

`PROXY=1` 이 `docker/compose.proxy.yml` 을 얹는다:

- **Caddy** 가 80·443 을 잡고 Let's Encrypt 인증서를 **자동으로 받고 갱신**한다
- 화면 컨테이너의 호스트 포트(`127.0.0.1:5177`)를 **닫는다** — 열어 두면 TLS 우회로가 남는다
- 인증서를 볼륨에 남긴다 (컨테이너 fs 에 두면 재기동마다 재발급 → 발급 한도에 걸린다)

⚠️ 인증서를 받으려면 도메인의 A 레코드가 **이 서버를 가리키고** 80·443 이 열려 있어야
한다. Let's Encrypt 가 그 주소로 되찾아온다.

## 4. 🔴 평문 HTTP 로는 로그인이 안 된다 (일부러 그렇다)

`apps/api/auth.secure_cookies` 가 `dev` 가 아닌 환경에서 `X-Forwarded-Proto: https` 가
아니면 **세션 만들기를 거부**한다.

배포해 놓고 HTTPS 를 안 걸면 세션 쿠키가 평문으로 오가는데 **화면은 멀쩡해서 아무도
모른다.** 앞단이 그 헤더를 안 넘기는 오설정도 여기서 걸린다 — 그때 로그인이 깨지는
것이 조용히 취약한 것보다 낫다.

⇒ 로그인 시도에서 `503 평문 HTTP 로는 세션을 만들지 않는다` 가 보이면 **앞단 문제**다.

## 5. 첫 로그인

1. `https://<도메인>` → 구글 로그인
2. `ADMIN_EMAILS` 에 적힌 계정은 **첫 로그인에 관리자**가 된다
3. 상단에 `이메일 · 관리자` 와 **계정** 탭이 보이면 성공

⚠️ 이미 있는 계정의 등급은 `ADMIN_EMAILS` 로 **안 바뀐다** — 설정 한 줄로 남의 등급이
바뀌면 관리자 화면의 기록(`approved_by`)과 사실이 갈린다.

## 6. 사람을 들이는 순서

```
가입 (구글 로그인)  →  pending  읽기만
       ↓ 관리자가 [승인]
                    viewer   읽기 (주문 못 함)
       ↓ 관리자가 [거래 허용] — 확인창이 한 번 더 뜬다
                    trader   주문·판 생성·청산
```

🔴 **승인과 거래 권한이 별개인 이유**: 승인은 목록을 훑으며 여러 건을 빠르게 누르는
동작이다. 그 손놀림에 실계좌 주문 권한이 딸려 나가면 안 된다.

## 7. 주문할 때 재인증 (요구 ③)

거래소로 주문이 나가는 경로(`/walkforward/live` · `/exchange/` · `/rebalancer`)는
마지막 구글 인증이 **5분 안**이어야 통과한다. 지나면 화면에 *"보안 확인이 필요하다"* 와
**다시 인증** 단추가 뜬다.

⛔ 조회와 사용자 관리에는 안 건다 — 거기까지 5분마다 요구하면 사람이 로그인만 하다 만다.

🔴 **조회에 안 거는 것은 실계좌에서도 그렇다.** 콘솔은 몇 초에 한 번씩
`/exchange/state` 를 당기므로, 읽기에 재인증을 걸면 **5분 뒤부터 화면 전체가 401** 이
되어 못 쓴다. 재인증의 뜻은 *"방금 사람이 거기 있었나"* 이고 그 질문이 필요한 것은
**돈이 움직일 때**다 — 보는 것은 되돌릴 수 없는 일이 아니고, 등급 문이 이미 앞에 서 있다.

## 8. 실계좌에서는 **읽기도** 조인다

사용자 확정 2026-08-30: *"승인 전까지는 read 만 있는데 실거래 콘솔이 보이는 건
옳지 않다."*

| 환경 | `/exchange/*` · `/walkforward/live/*` · `/rebalancer` 조회 |
|---|---|
| `dev` · `paper` | **읽기 권한** — 열람자도 페이퍼 콘솔을 본다 |
| `live` | **거래 권한** — 잔고·포지션·주문은 맡긴 사람만 본다 |

🔴 **경로가 아니라 환경이 문을 정한다.** 이 경로들은 지금 전부 테스트넷을 가리킨다
(`OrderGateway(AppEnv.PAPER)` · `GATE_TESTNET_*`). 그런데 실주문이 열리면(P2-8)
**같은 경로가 진짜 돈을 가리키게 된다** — 코드는 한 줄도 안 바뀌는데 뜻이 바뀐다.

⛔ **지금 막아 버리면 페이퍼 콘솔이 같이 죽는다.** 열람자에게 페이퍼 콘솔과 리포트를
주기로 한 것이 결정이고, 그 화면이 바로 이 경로들을 읽는다 (`/exchange/state` 하나만
봐도 페이퍼 콘솔과 관문 목록이 같이 쓴다).

⚠️ **나중에 기억해서 거는 문은 안 걸린다.** 실주문을 여는 날은 할 일이 많고, 그날
읽기 권한을 떠올릴 것이라고 기대하면 안 된다 — 그래서 미리 걸어 두고, 환경이 바뀌는
순간 저절로 조이게 했다. 목록은 `common/security/roles.py` 의 `MONEY_READ_PREFIXES` 다.

⛔ **설정을 못 읽으면 조인다** (`on_real_money`). 관대하면 오설정이 곧 구멍이고,
그 구멍은 조용하다 (절대 규칙 #8).

⚠️ 화면(`App.tsx`)도 라이브 콘솔 탭을 거래 권한에게만 보여 준다. 그것은 **편의**이고
방어는 위 표다 — URL 을 아는 사람은 화면을 안 거친다.

## 9. 데이터 영속 · 마이그레이션 · 백업 · 복구 (T213 · 2026-09-04)

> 사용자: *"데이터는 항상 유지되며, 이어져야 한다."*

### 무엇이 어디에 사는가

| 데이터 | 자리 | 컨테이너 재생성 | `down` | `down -v` | 서버 이전 |
|---|---|---|---|---|---|
| DB (캔들·RUN·원장·유저·설정) | 볼륨 `pgdata` | 유지 | 유지 | **유실** | 백업으로 |
| 펀드 원장 JSON · 산출물 · 로그 폴백 | 볼륨 `runlogs` (dev 는 `../logs` 바인드) | 유지 | 유지 | **유실** | 백업으로 |
| Redis | 볼륨 `redisdata` | 유지 | 유지 | 유실해도 됨 — **락 키 2개뿐** (실측 2026-09-04) | 불필요 |
| 백업 | 볼륨 `backups` | 유지 | 유지 | **유실** — 그래서 pgdata 와 다른 볼륨 | 옮긴다 |

⛔ `make down` 은 `-v` 를 절대 넣지 않는다 (Makefile 주석). 볼륨을 지우는 명령은 Makefile 에 없다.

### 마이그레이션은 기동의 일부다

`compose.base.yml` 의 일회성 `migrate` 서비스가 `alembic upgrade head` 를 돌리고, api·engine 은
`service_completed_successfully` 를 기다린다. **`make up` 만 하면 스키마가 올라간다** — `make migrate`
는 이제 개발 중 손으로 돌릴 때만 쓴다.

- 스키마가 안 맞으면 api 가 **아예 안 뜬다** (런타임에 터지는 것보다 낫다 · 규칙 #8)
- 컨테이너가 하나라 api·engine 이 동시에 돌리는 경합이 구조적으로 없다
- 확인: `docker compose ps -a` 에 `migrate  Exited (0)` · `docker compose logs migrate`

### 하위 호환 규약 (T219 · 2026-09-05 확정)

블루그린(§11)에서 **옛 api 와 새 api 가 같은 DB 를 잠시 같이 쓴다.** 마이그레이션은 새 코드가 뜨기 *전에*
돌므로, 옛 코드가 새 스키마 위에서 살아 있어야 한다. 규칙은 **확장 → (배포) → 수축** 이다.

| 하고 싶은 것 | 배포 N | 배포 N+1 |
|---|---|---|
| 컬럼 추가 | `nullable` 또는 `server_default` 로 추가 · 새 코드가 쓴다 | (필요하면) NOT NULL 로 조인다 |
| 컬럼 삭제 | 코드에서 읽기·쓰기를 **먼저 뗀다** (컬럼은 남긴다) | 컬럼을 지운다 |
| 컬럼 이름 변경 | 새 컬럼 추가 + 양쪽에 쓰기 + 백필 | 옛 컬럼 삭제 (위 행) |
| 타입 변경 | 새 컬럼으로 (이름 변경과 같다) | 옛 컬럼 삭제 |
| 테이블 삭제 | 코드에서 뗀다 | 지운다 |

⛔ **한 배포 안에서 금지**: ① 컬럼·테이블 삭제 ② 이름 변경 ③ `server_default` 없는 NOT NULL 추가 ④ 큰 테이블
(`candles`·`event_logs`) 의 잠금 거는 ALTER (테이블 재작성) — 옛 api 가 그 사이 죽거나 되돌림(§11)이 새 스키마
위에 옛 코드를 남긴다. 되돌림은 스키마를 되돌리지 않는다 — **그래서 N 단계는 늘 옛 코드와 호환이어야 한다.**

### 백업

`backup` 서비스(postgres:16 이미지 · `docker/backup.sh`)가 **기동 시 1회 + 24시간마다**:

```
/backups/updown-YYYYMMDDTHHMMZ.dump   pg_dump -Fc (압축 · 선택 복구 가능)
/backups/funds-YYYYMMDDTHHMMZ.tgz     logs/funds/*.json
보존 BACKUP_KEEP_DAYS (기본 7)
```

지금 당장 한 번: `docker compose ... run --rm backup once`. 실패하면 컨테이너가 exit 1 로 죽고
로그에 남는다 — 조용히 넘기지 않는다.

✅ **서버 밖 사본 = Lightsail 자동 스냅샷** (T219 · 2026-09-05 확정). 04:00 KST 에 디스크 통째(세 볼륨 포함)가 AWS
스냅샷 저장소로 간다 — 디스크 장애에도 남는다. 복구는 스냅샷에서 새 인스턴스를 띄우고 고정 IP 를 옮긴다. S3·로컬 pull 은
안 한다(두 번째 경로의 관리 비용 > 300 USDT 규모의 이득). 손으로 뽑을 때만:
`docker run --rm -v updown_backups:/b -v $PWD:/out alpine tar czf /out/backups.tgz -C /b .`

### 복구

```bash
# 연습 (운영 DB 안 건드림 · 새 DB 이름으로)
docker/restore.sh updown-20260904T0600Z.dump updown_drill

# 실제 (api·engine 을 내리고 운영 DB 에 덮어쓴 뒤 다시 띄운다)
docker/restore.sh updown-20260904T0600Z.dump
```

복구 뒤 스크립트가 `wf_runs · candles · users` 행 수를 찍는다 — **백업 시점의 수와 같아야 한다.**

### 연습 기록

| 날짜 | 방식 | 결과 |
|---|---|---|
| 2026-09-04 | dev · `once` 백업(46MB) → `updown_drill` 로 복구 11s → 행 수 비교 | ✅ wf_runs 298=298 · users 0=0 · candles 2,419,185 vs 원본 2,419,197 (백업 뒤 수집분 12) |

## 10. 배포 DB 정책 — 테스트 데이터는 올라가지 않는다 (T216 · 2026-09-04)

> 사용자: *"배포에 맞춰 DB 정리 (현재 테스트 데이터는 배포본에 안올라가게)."*

### 원칙

```
⛔ 개발 pgdata · runlogs 볼륨을 배포로 복사하지 않는다
✅ 배포 DB 는 빈 볼륨에서 시작한다:  migrate → seed_instruments → backfill
✅ 각 단계 뒤 verify_clean_db 가 "배포 시작 상태인가" 를 판정한다 — 어기면 멈춘다
```

"정리" 가 아니라 "처음부터" 인 이유: 무엇이 테스트 데이터인지 표마다 다르고(298개 판 ·
1,374 주문 · 957 감사 로그 · 2 펀드 파일), 정리 스크립트는 하나를 빠뜨린다. 빈 볼륨은
빠뜨릴 것이 없다.

### 표 분류 (`scripts/deploy/verify_clean_db.py`)

| 분류 | 표 | 배포 시작 시 |
|---|---|---|
| **비어야 한다** | `wf_runs` `wf_trades` `wf_orders` `wf_calibration` `backtest_runs` · `orders` `positions` `accounts` `account_balances` `portfolio_snapshots` `allocation_ledger` `approved_orders` `trade_proposals` `risk_plan_revisions` · `event_logs` `notifications` `structures` `transitions` | **0 행** — 하나라도 있으면 exit 1 |
| 참조 (다시 만든다) | `instruments` · `candles` | 시드 · 백필 (`--stage seeded` / `ready` 가 요구) |
| 사람이 넣는다 | `users` `broker_credentials` `app_settings` `risk_policies` | 세기만 — 첫 로그인·관리자 입력 |

파일 쪽: `logs/funds` `logs/walkforward` `logs/reconcile` `logs/labels` 에 파일이 있으면 실패 —
개발 산출물이 볼륨에 딸려 온 것이다.

### 절차

```bash
ENV=live ENV_FILE=.env.live scripts/deploy/init_db.sh          # 전체 (백필 포함 · 오래 걸림)
SKIP_BACKFILL=1 ENV=live ENV_FILE=.env.live scripts/deploy/init_db.sh   # seeded 까지
```

⚠️ **2.0.0 펀드는 `candles` 표를 쓰지 않는다** — 라이브 러너는 거래소 klines 를 직접 받는다
(`SEED_BARS`). `candles` 는 분석·차트 탭용이다. 그래서 백필을 건너뛰어도 펀드는 돈다.
다만 `ready` 검사는 백필 뒤에 따로 돌려야 한다. 백필 유니버스(`config/backfill.yml`)에
KRX·NASDAQ 이 있어 키가 없는 시장은 실패할 수 있다 — 배포처에선 유니버스를 코인으로 좁힌다
[결정 필요].

### 검사기 단독

```bash
# 컨테이너 안 (이미지에 들어 있다)
docker compose ... run --rm --no-deps api python scripts/deploy/verify_clean_db.py --stage migrated
# 호스트
set -a; . ./.env.live; set +a; uv run python scripts/deploy/verify_clean_db.py
```

개발 DB 에서 돌리면 **실패해야 정상**이다 (wf_runs 298 …). 그것이 검사기가 사는 증거다.

## 11. 무중단 재배포 — 블루그린 (T212 · 2026-09-04)

> 사용자: *"기능을 유지하며 다시 재배포가 가능한 형태."*

RUN 세션은 api 프로세스 안에 산다. 그래서 무중단의 뜻은 *프로세스가 안 죽는다* 가 아니라
**거래 리더가 끊기지 않는다** 다. api 슬롯이 둘(`api` · `api_b`)이고 트레이더 락이 리더를 정한다.

```bash
ENV=live ENV_FILE=.env.live scripts/deploy/bluegreen.sh
```

```
1 새 이미지 빌드
2 비어 있는 슬롯 기동 → healthy · 팔로워(trading_leader=false) 확인   실패 → 새 슬롯 내림
3 옛 슬롯 stop (grace 30s: 거래 루프 거두고 락 놓음)
4 새 슬롯 승격(trading_leader=true) → RUN 입양                       실패 → 옛 슬롯 다시 띄움
5 engine 재생성 (락 이양 · grace 60s) · web 갱신
```

- 겹치는 몇 초 동안 팔로워가 받은 거래 POST 는 **503** 이고 nginx 가 다른 슬롯으로 재시도한다.
  읽기는 팔로워도 답한다 (콘솔이 잠깐 비어 보일 수 있다).
- nginx 는 `resolve` 로 두 슬롯 주소를 5초마다 다시 푼다 — **api 재생성 뒤 web 재시작이 더는 필요 없다.**
- 🔴 마이그레이션은 **하위 호환**이어야 한다 — 2~3 사이에 옛 이미지와 새 이미지가 같은 DB 를 쓴다.
  컬럼 삭제·이름 변경은 두 배포에 걸쳐서 (먼저 안 쓰게 → 다음 배포에서 지운다).
- 리허설(dev · 테스트넷 RUN 11개): 36~40초 · RUN 11/11 입양 · api 핸드오프 중 `/api/health` 실패 0.

## 12. 1 GB 서버 준비 — Lightsail $7 · Ubuntu 24.04 (2026-09-04 사용자 결정)

실측 근거는 [T47](../planning/tasks/done/T47_resource_optimization.md). 1 GB 에서 지켜야 할 것은
다섯이다: **스왑 · 서버 밖 빌드 · postgres 감량 · 판 6개 고정 · engine 을 api 안에서**. 뒤의 셋은
`compose.live.yml` 이 이미 담고 있다 — live 는 engine 컨테이너가 뜨지 않고(프로필 `separate-engine`),
자원 비트는 리더 api 가 돌린다 (`UPDOWN_ENGINE_INPROC=1`). 자원 카드의 engine 행은 "engine(in api)" 로 뜬다.

### 12-1. 스왑 2 GB (빠뜨리면 블루그린 겹침 구간에 OOM)

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
echo "vm.swappiness=10" | sudo tee /etc/sysctl.d/90-updown.conf && sudo sysctl -p /etc/sysctl.d/90-updown.conf
free -m   # Swap: 2047
```

### 12-2. Docker (공식 저장소 · compose 플러그인 포함)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
docker compose version
```

### 12-3. 이미지는 받기만 한다

서버는 빌드하지 않는다. 릴리스는 GitHub Actions `release-images` 가 만든다:

```bash
# 로컬(개발 PC)에서 — 태그가 곧 배포 단위다
git tag v2026.09.04-1 && git push origin v2026.09.04-1
```

서버에서 GHCR 로그인은 **read:packages 권한만 있는 PAT** 로 한다 (저장소가 private 이라 필요하다):

```bash
echo "<PAT>" | docker login ghcr.io -u ericsj1998 --password-stdin
```

### 12-4. 처음 띄우기 · 그 뒤 재배포

```bash
git clone git@github.com:ericsj1998/up_and_down_invest.git ~/updown && cd ~/updown   # compose·스크립트만 쓴다
cp .env.example .env.live && nano .env.live                                        # §2
export IMAGE_TAG=v2026.09.04-1
bash scripts/deploy/init_db.sh live                                                # §10 — 빈 볼륨에서 migrate → seed → 백필
make up ENV=live PROXY=1                                                           # §3
# 이후 재배포는 항상
IMAGE_TAG=v2026.09.04-2 ENV=live bash scripts/deploy/bluegreen.sh                  # §11 — pull → 슬롯 교체
```

### 12-5. 올리는 기준 (계정 탭 자원 카드)

메모리 85% 가 하루 이상 · 스왑 사용 300 MiB 초과 · 판을 6개보다 늘릴 때 → $12 로 스냅샷 이전.
Static IP 는 새 인스턴스에 옮겨 붙으므로 DNS·거래소 화이트리스트는 그대로다. 중단 10~15분.

## 13. Demo Trading — 데모 API 를 따로 띄운다 (T221 · 2026-09-05)

실계좌 API 옆에 **데모 API**(`api_demo` · `APP_ENV=paper` · 테스트넷 키 · DB `updown_demo` · redis/1)가 하나 더 돈다.
nginx(web) 가 `updown_mode` 쿠키로 갈라 보내고, `/api/auth/guest` 는 늘 데모다. 게스트(구글 없음 · 읽기만)는 데모만 본다.

| 준비 | 내용 |
|---|---|
| 플랜 | **$7 유지**(사용자 결정) — 데모 API 실측 97 MiB(판 0) · 상한 256M. 예상 합계 700~850 MB / 911 MiB + 스왑 2G. 자원 창에서 스왑이 늘면 $12 |
| `~/updown/.env.demo` | `.env.demo.example` 대로. **`GATE_API_*` 절대 금지**(paper 프로세스는 라이브 키가 보이면 기동 거부). `GOOGLE_*`·`SESSION_SECRET`·`ADMIN_EMAILS` 는 `.env.live` 와 같은 값 |
| 테스트넷 키 | IP 화이트리스트에 서버 Static IP |
| DB | `migrate_demo` 가 `updown_demo` 를 없으면 만들고 스키마를 올린다 (기존 볼륨에서도) |
| 확인 | `curl -X POST https://<도메인>/api/auth/guest` → 200 · 그 쿠키로 `/api/auth/me` → `guest:true, mode:demo` · 쿠키를 `updown_mode=live` 로 바꿔 `/api/auth/me` → `signed_in:false` (실계좌는 게스트 쪽지를 거른다) |

⚠️ `bluegreen.sh` 는 `api`·`web` 슬롯만 다룬다 — `api_demo`·`migrate_demo` 는 `docker compose … up -d migrate_demo api_demo` 로 따로
올린다(⬜ 스크립트에 넣기). 데모 API 는 블루그린 대상이 아니다(내려가도 돈이 안 움직인다).

## 남은 것 (아직 안 함)

| 항목 | 왜 남았나 |
|---|---|
| `event_logs` 에 `user_id` | 누가 무엇을 했는지 감사 로그에 아직 안 남는다 |
| 세션 강제 만료 | 등급 회수는 즉시 듣지만(표에서 읽으므로) 쪽지 자체는 12시간 산다 |
| 실패 로그인 제한 | 구글이 앞에서 막으므로 급하지 않다 |
