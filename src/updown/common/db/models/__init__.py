"""§9 전 테이블 ORM (spec §9 — 20개).

**이 모듈을 import 하면 `Base.metadata` 가 완성된다.** Alembic `env.py` 와
`tests/test_migrations.py` 가 이 사실에 의존하므로, 새 모델 파일을 추가하면
여기 import 도 함께 추가해야 한다. 빠뜨리면 autogenerate 가 그 테이블을
"삭제 대상"으로 인식한다.
"""

from updown.common.db.models.accounts import (
    Account,
    AccountContact,
    AiParticipant,
    AssistantDraft,
    ChatThread,
    MarketGrantRow,
    PlaybookGrantRow,
    RoleCollection,
)
from updown.common.db.models.analysis import AnalysisReport, Structure
from updown.common.db.models.fundamentals import FinancialFactRow
from updown.common.db.models.market import Candle, CandleQualityIssue
from updown.common.db.models.master import BrokerCredential, Instrument, User
from updown.common.db.models.ops import (
    AppSetting,
    BacktestRun,
    EventLog,
    Notification,
    StockPaperAccount,
)
from updown.common.db.models.portfolio import (
    AccountBalance,
    AllocationLedgerEntry,
    PortfolioSnapshot,
)
from updown.common.db.models.trading import (
    ApprovedOrder,
    Order,
    Position,
    RiskPlanRevision,
    RiskPolicy,
    TradeProposal,
    Transition,
)
from updown.common.db.models.walkforward import (
    WalkforwardCalibration,
    WalkforwardOrder,
    WalkforwardRun,
    WalkforwardTrade,
)

__all__ = [
    "Account",
    "AccountBalance",
    "AccountContact",
    "AiParticipant",
    "AllocationLedgerEntry",
    "AnalysisReport",
    "AppSetting",
    "ApprovedOrder",
    "AssistantDraft",
    "BacktestRun",
    "BrokerCredential",
    "Candle",
    "CandleQualityIssue",
    "ChatThread",
    "EventLog",
    "FinancialFactRow",
    "Instrument",
    "MarketGrantRow",
    "Notification",
    "Order",
    "PlaybookGrantRow",
    "PortfolioSnapshot",
    "Position",
    "RiskPlanRevision",
    "RiskPolicy",
    "RoleCollection",
    "StockPaperAccount",
    "Structure",
    "TradeProposal",
    "Transition",
    "User",
    "WalkforwardCalibration",
    "WalkforwardOrder",
    "WalkforwardRun",
    "WalkforwardTrade",
]

#: spec §9 가 규정한 테이블 수.
SPEC_9_TABLE_COUNT = 20

#: 모의 라이브 판 영속화 (T16 ②) — `wf_runs` · `wf_trades` · `wf_orders` + `wf_calibration`
#: (T185 교정 원장 · 2026-08-31).
#:
#: ⚠️ **§9 밖이다.** 스펙이 규정한 20개와 섞어 세면 "§9 를 다 만들었나"에 답할 수 없다.
WALKFORWARD_TABLE_COUNT = 4

#: 앱 전체가 공유하는 설정 (T21 ⑦) — `app_settings`.
#:
#: ⚠️ **§9 밖이고 판별 표도 아니다.** 금고 한도처럼 *"모든 판에 같이 걸리는 값"* 이
#: 여기 산다 — 판 표에 두면 판마다 다른 한도가 생겨 금고가 배로 탄다.
SETTINGS_TABLE_COUNT = 1

#: 로그인 계정 (T37 · 2026-08-29) — `accounts`. §9 의 `users` 와 다르다: 구글 이메일 · 등급 · 승인.
#: + `account_contacts`(T227 · 0110 · 보류 문의) + `role_collections`(T228 · 0112 · 권한 묶음).
#: ⚠️ 두 표를 더하고 여기를 안 올려 2026-09-07~08 CI 가 붉었다 (1.3.1 에서 고침).
#: + `playbook_grants`(T230 · 0115 · 매매법별 권한 덮어쓰기).
#: + `market_grants`(T242 · 0119 · 시장 갈래별 권한 덮어쓰기).
#: + `assistant_drafts`(T247 · 0121 · 온보딩 위저드 초안 — 사람마다 한 행 · 계정 저장소와 같은 축).
#: + `chat_threads`(T248 · 0122 · AI 채팅 대화 — 사람마다 여럿).
ACCOUNTS_TABLE_COUNT = 7

#: `tests/test_migrations.py` 가 실제 DB 와 대조하는 총합.
#:
#: 🔴 표를 더하면 **여기도 더한다** — 2026-09-04 까지 두 표(`wf_calibration` · `accounts`)가 빠진 채
#: 24 로 남아 `make ci` 가 빨간 상태였다. 이 합이 곧 "모델 = 마이그레이션 = DB" 의 대조표다.
#: `stock_paper_accounts`(T240 · 0118 · 주식 페이퍼 계좌 — 시장당 JSONB 한 행).
STOCK_PAPER_TABLE_COUNT = 1

#: `financial_facts`(T243 · 0120 · 공시 사실 원자료 — 지표가 아니라 사실). §9 밖.
FUNDAMENTALS_TABLE_COUNT = 1

EXPECTED_TABLE_COUNT = (
    SPEC_9_TABLE_COUNT
    + WALKFORWARD_TABLE_COUNT
    + SETTINGS_TABLE_COUNT
    + ACCOUNTS_TABLE_COUNT
    + STOCK_PAPER_TABLE_COUNT
    + FUNDAMENTALS_TABLE_COUNT
)
