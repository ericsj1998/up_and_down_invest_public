# WhaleSurfer

유명 투자자(13F 보고자 104곳)의 보유 지도 — **누가 무엇을 들고 있고, 무엇을 사고팔았나**를 보여 준다.
업앤다운의 둘째 제품 후보(T442). 2026-10-09 본체에서 떼어 냈다(T442 §1-1 ②).

> 🔴 **분석 · 안내만 한다.** 주문 경로가 없다. 13F 는 롱 보유만 · 분기 끝 45일 뒤 공개다.
> 공시 뒤 따라 산 종목은 평균적으로 시장(SPY)과 같았다 — 산 종목과 판 종목의 다음 분기 수익이 같았다
> (T443 · 2013 ~ 2026 · 사건 154만). 화면의 "공시 뒤 따라 샀다면" 칸은 그 사실을 종목마다 보여 준다.

## 어디에 무엇이 있나

| 경로 | 무엇 |
|---|---|
| `src/whalesurfer/edgar/` | 13F-HR 읽기(`thirteen_f.py`) · CUSIP → 티커(`figi.py` · OpenFIGI 키 없이) |
| `src/whalesurfer/portraits.py` | 위키백과 초상 + 저작자 · 라이선스 |
| `src/whalesurfer/api/` | 라우터(`routes.py`) · 단독 앱(`app.py`) |
| `whalesurfer/web/` | 화면 — 별도 Vite 앱(5174) · 본체 화면과 코드 공유 없음 |
| `whalesurfer/scripts/` | 큐레이션 · 13F 역사 적재 · 토스 일봉 적재 · T443 백테스트 · 날짜별 진단 |
| `config/whalesurfer/managers.yml` | 보고자 104 · 수동 CUSIP → 티커 (공개 저장소 동기화 제외 · D6) |
| `cache/whalesurfer/` | 13F 역사 · 티커 · 사진 · T443 실현값 (gitignore) |
| `tests/test_whalesurfer.py` | 시험 |
| `docs/planning/tasks/T442_whalesurfer.md` · `T443_whalesurfer_backtest.md` | 결정 · 백테스트 |

## 본체와의 경계

- 본체(`updown`)는 이 패키지를 **import 하지 않는다** — `.importlinter` 계약 5.
- 이 패키지는 본체의 공용층(`updown.common` · `updown.marketdata`)만 빌린다 — 계약 6.
- 실계좌 서버 이미지에 **안 들어간다** — `.dockerignore`(D7 "서버에는 아예 안 띄움").
- 토스 일봉 적재(`scripts/whalesurfer_ingest_prices.py`)는 본체 DB · 토스 프록시를 쓰는 **1회용 연구 도구**다.
  제품이 서면 일봉은 자기 출처로 간다(토스는 개인 증권 키라 공개 서비스가 기대면 안 된다).

## 로컬 실행 (연구 PC · WSL · 저장소 루트)

```bash
set -a; . ./.env.dev; set +a                                   # EDGAR_USER_AGENT 만 쓴다
uv run --no-sync uvicorn whalesurfer.api.app:app --port 8010   # API
cd whalesurfer/web && npm install && npm run dev               # 화면 http://localhost:5174
```

🔴 인증이 없다 — 로컬 전용. 공개하려면 T442 §1-1 ③(별도 서버 · 인증 · 요금제)이 먼저다.
첫 화면에서 "합의 자료"(104 보고자 x 2분기)는 1분쯤 걸리고 API 가 6시간 기억한다.

## 저장소를 떼어 낼 때 (§1-1 ④)

위 표의 경로를 옮기고, 본체에서 빌린 공용층(`common/config` · `common/http/outbound` · `common/cache` ·
`common/logging` · `marketdata/fundamentals/client` · `provider.edgar_client`)을 복사하거나 작은 공용 패키지로 뺀다.
