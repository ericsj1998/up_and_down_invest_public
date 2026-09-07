# 매매법 작성 가이드 — 탐지기 · 룰 설정 · 플레이북 (2026-09-07)

> 새 매매법 하나는 **파일 세 개 + 등록 한 줄**이다. 그 밖의 코드를 고쳐야 한다면 이 가이드가 틀렸거나
> 플러그인 계약이 깨진 것이다 — 그때는 코드가 아니라 계약을 고친다 (spec §4.3.1).
> 예시 매매법 [`sample_ma_cross`](../../src/updown/analysis/detectors/sample_ma_cross.py) 가 이 가이드의 실물이다.

---

## 0. 먼저 정할 것 — 이것이 매매법인가, 플랫폼인가

| 매매법 (비공개 `updown-strategy` · 또는 예시) | 플랫폼 (공개) |
|---|---|
| 언제 들어가나 · 어디에 손절을 두나 · 얼마나 사나(비중·배율) · 언제 나가나 | 봉을 받고 · 주문을 내고 · 원장에 적고 · 거래소와 맞추고 · 화면에 그린다 |
| `detectors/<id>.py` · `config/rules/<id>.yml` · `config/playbooks.yml` 의 선언 | 레지스트리 · 세션 · 러너 · 위험 정책 · 지표·구조물 수학 |

임계값·배수·기간은 **코드에 박지 않는다** — 룰 설정(YAML)에서 `RuleParams` 로 들어온다 (§4.3.1).
값은 표준 이론값으로 고정하고 성과를 보고 미세조정하지 않는다 (§5.6.1 ② · §5.6.2). 바꾸면 **새 버전**이다.

---

## 1. 파일 세 개

### ① 탐지기 — `src/updown/analysis/detectors/<rule_id>.py` (비공개 패키지면 `updown_strategy/detectors/`)

필수 요소:

```python
RULE_ID = "<rule_id>"          # 파일명 · 룰 설정 파일명 · detector.id 가 전부 같아야 한다
RULE_VERSION = "0.1"           # 구성이 바뀌면 올린다 — 같은 이름에 다른 성적이 섞이면 안 된다 (§5.6.2)
SETUP_TYPE = "<SETUP_TYPE>"    # TradeSetup.setup_type — 성과 귀속의 열쇠

def <rule_id>_setup(window, timeframe, round_trip, *, <params>) -> TradeSetup | None:
    """순수 함수. 봉 창(오래된 것 → 마지막 마감 봉) 만 보고 셋업 하나를 내거나 None."""

@dataclass(frozen=True, slots=True)
class <Name>Detector(RuleDetectorBase):
    rule_params: RuleParams
    RULE_VERSION: ClassVar[str | None] = RULE_VERSION
    def detect(self, ctx: MarketContext) -> list[TradeSetup]: ...   # 시간축마다 _setup 을 부른다

def build(params: RuleParams) -> <Name>Detector: ...                  # 레지스트리가 부르는 팩토리
def register() -> tuple[tuple[str, DetectorFactory], ...]:            # entry point 가 부르는 것
    return ((RULE_ID, build),)                                        # 한 모듈이 여러 id 를 맡을 수 있다
```

`TradeSetup` 에 **반드시** 넣는 것 (`common/domain/setup.py`):

| 칸 | 규칙 |
|---|---|
| `stop_loss` · `stop_candidates` | 손절은 늘 있다. 없는 셋업은 내지 않는다 (절대 규칙 #3·#4 — 확정은 RiskManager 가 하지만 제안에 손절이 없으면 사이징이 안 된다) |
| `entry_plan` (`EntryLeg` 들) | 비중(`ratio`)의 합이 1. 사다리(지정가 여러 다리)는 룰 설정 `limit_entry: true` · `limit_legs: N` 과 짝 — 플랫폼이 첫 다리 예산을 `1/limit_legs` 로 잡는다 |
| `entry_trigger` | `CLOSE_CONFIRM`(종가 확인 · 시장가) 또는 `TOUCH`(지정가 닿음) |
| `tp_ladder` | 목표들. 비율 합 1. 반익·본절은 `TpFollowUpAction` |
| `rr_ratio` · `confidence` · `evidence` | 근거(`Evidence(source=RULE_ID, family, detail, grade)`) 는 화면·리포트가 그대로 보인다 — 사람이 읽을 문장으로 |

하지 말 것:

- **미래 봉을 보지 않는다.** 창의 마지막 봉이 "지금 막 닫힌 봉" 이다. `analysis.evaluation`(이행률 측정)은 import 금지 — import-linter 계약 3 이 막는다.
- **현재 시각·난수를 쓰지 않는다** (절대 규칙 #5). 같은 창이면 같은 답이다.
- **비용을 못 갚는 자리는 자리가 아니다.** 손절 거리 / 진입가 < 왕복 비용(`round_trip`)이면 None (§1-0b).
- 지표는 `analysis/indicators` 것을 쓴다(`sma` · `atr` · `adx` …). pandas·numpy 는 런타임 코드에 못 들어온다 (절대 규칙 #9 · 시험이 막는다).
- 같은 자리를 봉마다 되풀이하지 않는다 — "막 일어났다"(직전 봉과 비교) 를 조건에 넣는다.

### ② 룰 설정 — `config/rules/<rule_id>.yml` (비공개 패키지면 그 안의 `rules/`)

```yaml
# 무엇을 재는 룰인지 한 줄 · 값의 출처(문서/실측) 한 줄
rule_id: <rule_id>        # 파일명과 같아야 한다 — 다르면 로더가 거부한다
version: "0.1"
enabled: true             # false 면 레지스트리가 뺀다 (화면의 룰 문서에는 남는다)
buckets: [swing]          # scalp | swing | longterm — 어느 시간축 버킷에서 도나
params:
  fast: 20                # 값마다 왜 그 값인지 주석 — 이 파일이 값의 유일한 출처다
  limit_entry: false      # 지정가 사다리면 true + limit_legs
```

허용 키는 `rule_id · version · enabled · buckets · params` 다섯뿐이다(`detectors/rules.py`). `params` 는 자유형이고
탐지기가 `_iparam/_dparam` 같은 읽기 함수로 꺼낸다 — 기본값은 코드에 두되 설정이 있으면 설정이 이긴다.

### ③ 플레이북 선언 — `config/playbooks.yml` (비공개 패키지면 그 안의 `playbooks.yml`)

```yaml
  <playbook_id>:
    version: "0.1.0"
    listed: true            # 콘솔 선택창에 보이나
    recommended: false      # 첫 recommended 가 기본 전략이 된다 — 예시는 false
    leverage: 2             # 선언 배율 — 화면 입력칸이 없다. 이 값이 유일한 출처
    label: 사람이 읽는 이름 (수치 요약 포함)
    market_groups: [코인]
    timeframe: 4h           # 판정 축 — 봉이 닫힐 때 판정한다
    regimes: [RANGE, UPTREND, DOWNTREND]   # 어느 국면에서 도나 — 비어 있으면 어디서도 안 돈다
    primary_family: TREND
    primary_flags: [setup.<rule_id>]
    setups: [<rule_id>]     # 이 플레이북이 부르는 룰 id 들 — 전부 레지스트리에 있어야 한다
    risk_pct: "0.005"       # 건당 리스크 — 사이징의 분자
    backtest_note: "…"      # 측정 근거 한 줄 — 콘솔 펀드 만들기 카드에 그대로 보인다
```

⚠️ **국면은 한 단계 위 시간축의 추세**에서 온다(`playbook_run.major_trend` · 4h 면 일봉 · 일봉은 210봉 이상 필요).
급전에 그 축이 없으면 국면이 None 이라 어떤 플레이북도 안 돈다 — 백테스트 데이터에 상위 축을 넣는다.

선언 칸의 뜻은 `analysis/playbook/types.py` 의 `Playbook` docstring 이 하나씩 적어 두었다(`trail_ma` · `adx_exit_*` · `relever` …).
칸 이름은 공개지만 **값**은 매매법이다.

---

## 2. 등록 한 줄 — entry point

패키지의 `pyproject.toml`:

```toml
[project.entry-points."updown.detectors"]
<rule_module> = "<package>.detectors.<rule_module>:register"

# 비공개 패키지는 설정·플레이북도 같은 방식으로 알린다 (함수는 Path 를 돌려준다)
[project.entry-points."updown.rules"]
strategy = "<package>.plugin:rules_dir"
[project.entry-points."updown.playbooks"]
strategy = "<package>.plugin:playbooks_file"
```

레지스트리(`analysis/plugins.py` → `detectors/registry.py`)가 설치된 패키지 메타데이터에서 찾는다.
그래서 **`uv sync`(또는 이미지 재빌드)가 있어야 보인다** — 파일만 두고 "안 뜬다" 는 대부분 이것이다.

레지스트리가 부팅 때 확인하는 것: 등록된 id 마다 설정이 있나 · 설정마다 등록이 있나 · id 중복 · `detector.id == 등록 키`.
하나라도 어긋나면 **부팅이 멈춘다** — 조용히 반쪽으로 도는 것보다 낫다 (절대 규칙 #8).

---

## 3. 시험 — 없으면 없는 매매법이다

| 시험 | 무엇을 | 본보기 |
|---|---|---|
| 결정론 골든 | 손으로 만든 봉 창에서 "막 일어난 자리" 에만 셋업 · 손절/목표 산식 · 워밍업 · 방향 옵션 | `tests/test_sample_ma_cross.py` |
| 배선 | entry point 에 있다 · 설정과 짝이다 · 플레이북이 부른다 | 같은 파일 `TestItIsWiredLikeAnyOtherRule` |
| 끝까지 | 세션이 그 플레이북으로 진입 → 청산까지 (상위 축 봉 포함) | `tests/test_sample_end_to_end.py` |

측정(백테스트·합성 미래)은 [backtest_guide.md](backtest_guide.md). 후보가 여럿이면 **한 번에 한 축** · 표본 30 미만이면 기존 유지 ·
out-of-sample 로 판정한다 (절대 규칙 #12). 성과 표에는 항상 MDD 와 λ 를 함께 적는다.

---

## 4. 점검표 (커밋 전)

- [ ] `rule_id` = 파일명 = 룰 설정 파일명 = `detector.id`
- [ ] 손절 없는 셋업이 나올 수 있는 경로가 없다
- [ ] 마지막 봉 이후를 보는 코드가 없다 (`window[-1]` 이 마지막)
- [ ] 숫자 상수는 룰 설정에 있고, 코드의 기본값과 같다
- [ ] `register()` + entry point + `uv sync`
- [ ] `make ci` 초록 · `make strategy-scan` 이 공개 트리에서 이 이름을 안 잡는다(비공개면) 또는 `public_ids` 에 있다(예시면)
- [ ] 플레이북 `backtest_note` 에 측정 출처가 있다 — 없으면 "측정 없음" 이라고 적는다
