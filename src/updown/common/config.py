"""환경 설정과 시크릿 로딩 (spec §12.4, §8, §7).

두 가지를 강제한다:

1. **fail-fast** — 필수 키가 없으면 기동하지 않는다. 조용한 기본값으로 넘어가면
   "왜 dev DB 에 붙었지?" 를 나중에 디버깅하게 된다 (spec §7).
2. **live 시크릿 격리** — `APP_ENV != live` 프로세스는 live 자격증명에 **닿을 수 없다.**
   접근 시도는 예외이며, 애초에 환경에 live 키가 있으면 기동 자체를 거부한다 (spec §12.4).

두 번째가 이 모듈의 핵심이다. dev 프로세스가 실수로 `.env.live` 를 로드했을 때
"조용히 잘 돌아가는" 것이 최악의 결과다.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    """실행 환경 (spec §12.4 — dev / paper / live 완전 분리).

    Note:
        **기본값을 주지 않는다.** 기본값이 있으면 환경변수 누락이 조용히 dev 로
        떨어지고, 반대로 live 로 떨어지면 사고다. 명시 강제가 유일하게 안전하다.
    """

    DEV = "dev"
    PAPER = "paper"
    LIVE = "live"


class ConfigurationError(RuntimeError):
    """설정이 잘못되어 기동할 수 없다 (spec §7 조용한 실패 금지)."""


class LiveSecretAccessError(ConfigurationError):
    """live 자격증명에 비-live 환경에서 접근했다 (spec §12.4).

    Note:
        `ConfigurationError` 를 상속하므로 부팅 가드에서 함께 잡히지만, 별도 타입이라
        테스트가 "격리 위반"과 "설정 누락"을 구분해 검증할 수 있다.
    """


#: live 환경에서만 존재해도 되는 필드. 그 외 환경에 값이 있으면 기동을 거부한다.
#:
#: ⚠️ **조회 전용 자격증명(`toss_marketdata_*`)은 여기 없다.** 아래 주석 참조.
LIVE_ONLY_FIELDS: tuple[str, ...] = (
    "toss_client_id",
    "toss_client_secret",
    "toss_cert_path",
    "toss_cert_key_path",
)

#: 조회 전용 토스 자격증명 — **주문 경로에서 쓰지 않는다.**
#:
#: ## 왜 네임스페이스를 나누는가
#:
#: 백테스트용 과거 캔들을 받으려면 dev/paper 프로세스가 토스를 호출해야 한다. 그런데
#: §12.4 격리는 비-live 환경에 `TOSS_*` 가 **있기만 해도** 기동을 거부한다 — 그 규칙을
#: 풀면 live 주문 자격증명이 dev 로 새는 문을 여는 것이다.
#:
#: 그래서 규칙을 풀지 않고 **이름을 나눈다**:
#:
#: | 이름 | 쓰임 | 환경 제약 |
#: |---|---|---|
#: | `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET` / 인증서 | **주문** | live 전용 (불변) |
#: | `TOSS_MARKETDATA_CLIENT_ID` / `..._SECRET` | **조회**(캔들·호가) | 어느 환경이든 |
#:
#: 값이 같은 문자열일 수 있다는 것이 이 분리를 무의미하게 만들지 않는다 — 게이트가 보는
#: 것은 **선언**이고, 조회 네임스페이스만 가진 프로세스는 `TossCredentials`(주문 묶음)를
#: **구성할 수 없다**. 주문 차단의 실질 방어선은 여전히 `OrderGateway` 독점과 어댑터의
#: 예외 두 층이다 (절대 규칙 #0, P0-7-6).
MARKET_DATA_ONLY_FIELDS: tuple[str, ...] = (
    "toss_marketdata_client_id",
    "toss_marketdata_client_secret",
)

#: 빈 값이 곧 "미설정"인 선택 필드.
#:
#: `.env` 파일에 자리만 잡아 둔 키(`TOSS_CLIENT_SECRET=`)는 흔하다. 이것을 빈 문자열로
#: 받으면 `None` 검사를 전부 통과해 **미수령 자격증명이 "설정됨"으로 오인된다.**
#: 실제로 현재 `.env.live` 가 그 상태다 (Client Secret·인증서 미수령 — X-1 ⓪).
_OPTIONAL_FIELDS: tuple[str, ...] = (
    "upbit_access_key",
    "upbit_secret_key",
    "dart_api_key",
    "edgar_user_agent",
    # 이메일 리포트 (T35) — 자리만 잡힌 키는 없는 것으로 읽는다
    "smtp_host",
    "smtp_user",
    "smtp_password",
    "notify_from_email",
    "report_to",
    # 구글 로그인 (2026-08-30) — `.env` 에 자리만 잡힌 키를 "설정됨" 으로 읽으면
    # 로그인이 반쯤 켜진 채 돈다. 빈 문자열은 없는 것으로 정규화한다.
    "google_client_id",
    "google_client_secret",
    "google_redirect_uri",
    "session_secret",
    "admin_emails",
    "accounts_database_url",
    *LIVE_ONLY_FIELDS,
    *MARKET_DATA_ONLY_FIELDS,
    # 토스 프록시 (T275) — 자리만 잡힌 키는 없는 것
    "toss_proxy_url",
    "toss_proxy_token",
    # 주요 일정 달력 (T276) — 자리만 잡힌 키는 없는 것 · 없으면 달력이 이유와 함께 빈 칸
    "fred_api_key",
    "finnhub_api_key",
)


class TossCredentials:
    """토스 자격증명 묶음 (spec §2.2, §12.4).

    Note:
        `Settings.toss_credentials` 로만 얻을 수 있고, 그 프로퍼티가 live 환경을
        확인한다. 자격증명을 개별 필드로 흩어 읽으면 그 검사를 우회하게 되므로
        묶어서 하나의 관문으로 만든다.
    """

    def __init__(
        self,
        client_id: SecretStr,
        client_secret: SecretStr,
        cert_path: Path,
        cert_key_path: Path,
    ) -> None:
        """자격증명을 담는다.

        Args:
            client_id: 토스 Client ID.
            client_secret: 토스 Client Secret.
            cert_path: mTLS 인증서 경로.
            cert_key_path: mTLS 인증서 키 경로.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.cert_path = cert_path
        self.cert_key_path = cert_key_path

    def __repr__(self) -> str:
        """시크릿을 노출하지 않는 표현 (spec §8)."""
        return "TossCredentials(client_id=SecretStr('**********'), ...)"


class Settings(BaseSettings):
    """애플리케이션 설정.

    Attributes:
        app_env: 실행 환경. **필수, 기본값 없음.**
        database_url: PostgreSQL 접속 URL (psycopg3).
        redis_url: Redis 접속 URL.
        log_level: 로그 레벨.
        upbit_access_key: 업비트 조회용 access key.
        upbit_secret_key: 업비트 조회용 secret key.
        toss_client_id: 토스 Client ID — **live 전용**.
        toss_client_secret: 토스 Client Secret — **live 전용**.
        toss_cert_path: mTLS 인증서 경로 — **live 전용**.
        toss_cert_key_path: mTLS 인증서 키 경로 — **live 전용**.
        dart_api_key: DART OpenAPI 키 (P3-1).
        edgar_user_agent: SEC EDGAR 요청의 User-Agent — `이름 이메일` (T243).

    Note:
        시크릿은 전부 `SecretStr` 이다 — 로그·`repr` 에 평문이 새지 않는다 (spec §8).
        평문이 필요한 지점에서만 `.get_secret_value()` 를 부르고, 그 호출은 로깅
        경로에 두지 않는다.
    """

    model_config = SettingsConfigDict(
        env_file=None,  # 파일 로딩은 프로세스 진입점(compose env_file / set -a)의 책임
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv
    database_url: str
    redis_url: str
    accounts_database_url: str | None = None
    """계정·문의·관리자 설정 표를 **다른 DB** 에서 연다 (사용자 2026-09-07 "유저 풀 공유").

    데모 API 가 이 값으로 실계좌 DB 의 `accounts`·`account_contacts`·`app_settings` 를 읽고
    쓴다 — 사람은 한 명이고 실계좌냐 데모냐는 권한의 종류다. 비면 `database_url` 과 같은 DB.
    ⚠️ 원장(`wf_*`)·캔들은 여전히 자기 DB — 계정 저장소만 갈아 끼운다 (`auth.attach_accounts`).
    """
    log_level: str = "INFO"

    upbit_access_key: SecretStr | None = None
    upbit_secret_key: SecretStr | None = None

    toss_client_id: SecretStr | None = None
    toss_client_secret: SecretStr | None = None
    toss_cert_path: Path | None = None
    toss_cert_key_path: Path | None = None

    # 조회 전용 — 주문 경로에서 쓰지 않는다 (`MARKET_DATA_ONLY_FIELDS` 주석).
    toss_marketdata_client_id: SecretStr | None = None
    toss_marketdata_client_secret: SecretStr | None = None

    # 토스 프록시 (T275 · 2026-09-11) — 이 프로세스가 토스를 직접 안 부르고
    # 실계좌 서버의 `/admin/toss/result` 를 개인 토큰으로 부른다. 토스 토큰은
    # client 당 하나라 발급 주체를 서버 하나로 고정하기 위해서다. 둘 다 있어야 켜진다.
    toss_proxy_url: str | None = None
    toss_proxy_token: SecretStr | None = None

    toss_rate_per_second: int = 5
    """토스 요율 그룹당 초당 요청 수 — 토스 스펙이 수치를 안 주어 보수적으로 5 (2026-09-11 사용자
    "30초 너무 길다": 1h 400봉 = 1분봉 200페이지 = 5건/초로 40초). 서버에서 올려 보고 429 가 없으면
    유지한다 — `Outbound` 가 429 의 `Retry-After` 로 물러난다. 0 이하면 기본값."""

    # 주요 일정 달력 (T276 · 2026-09-13) — FRED 는 지표 발표 **예정일**, Finnhub 는 실적 **예정일**.
    # 값·확정은 이미 있는 BLS·EDGAR 가 맡는다. 둘 다 없어도 뜬다 — 그 출처만 이유와 함께 빈 칸.
    fred_api_key: SecretStr | None = None
    finnhub_api_key: SecretStr | None = None

    dart_api_key: SecretStr | None = None

    edgar_user_agent: str | None = None
    """SEC EDGAR 가 요구하는 User-Agent(`이름 이메일`) — 없으면 403 (T243).

    시크릿이 아니다(공개 헤더). 비면 재무 새로고침이 503 으로 멈춘다 — 조용한 403 반복보다 낫다
    (규칙 #8).
    """

    # ── Gate 실계좌 (T157 · 2026-09-04) — `.env.live` 에만 (절대 규칙 #1) ──
    gate_api_key: SecretStr | None = None
    """Gate 실계좌 API 키 — 선물 R/W 만 · 출금 OFF · IP 화이트리스트.

    `execution/gateway.live_adapter` 만 읽는다.
    """

    gate_api_secret: SecretStr | None = None

    live_orders: bool = False
    """운영자 스위치 (`LIVE_ORDERS=1`). 키가 있어도 이것이 꺼져 있으면 주문은 테스트넷으로 간다."""

    stock_live_orders: bool = False
    """주식 실주문 스위치 (`STOCK_LIVE_ORDERS=1` · T240). 코인 스위치와 **따로** 다.

    켜져 있어도 토스 실주문 어댑터는 아직 없다(Secret·인증서 미수령 · G1 뒤) — 켜면 주식 판은
    페이퍼로 떨어지지 않고 **예외**다 (`execution/gateway.live_adapter`).
    """

    run_start_request_cap: int = 300
    """판 시작 한 번의 브로커 요청 상한 (`RUN_START_REQUEST_CAP` · T253). 0 이면 무제한.

    폴링 브로커(토스)는 봉을 1분봉에서 합성하므로 워밍업 600봉이 축에 따라 요청 수백 개다
    (실측 4h 판 454요청 → 데모 API 재시작). 넘으면 판을 띄우지 않고 503 으로 사람에게
    말한다(규칙 #8).
    """

    # ── 이메일 리포트 (T35) — 값은 .env 에만 (절대 규칙 #1) ──
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    notify_from_email: str | None = None
    report_to: str | None = None
    """기본 수신자 — 쉼표로 여럿."""
    report_hour_kst: int = 9
    """일간 자동 발송 시각 (KST 시). 저장·스케줄은 UTC 로 변환해 돈다 (규칙 #7)."""

    # ── 구글 로그인 (2026-08-30 배포 준비) — 값은 .env 에만 (절대 규칙 #1) ──
    google_client_id: str | None = None
    """구글 OAuth 클라이언트 ID. **비밀이 아니다** (브라우저 주소창에 실려 나간다)."""

    google_client_secret: SecretStr | None = None
    """구글 OAuth 클라이언트 시크릿 — 서버끼리의 코드 교환에만 쓴다."""

    google_redirect_uri: str | None = None
    """구글이 되돌려 보낼 주소. **구글 콘솔에 등록한 값과 글자까지 같아야** 한다.

    ⚠️ 여기서 파생하지 않고 명시로 받는다 — 프록시 뒤라 서버는 자기 바깥 주소를
    정확히 모른다. 추측한 값이 콘솔과 1글자만 달라도 구글이 `redirect_uri_mismatch`
    로 거절하고, 그 오류는 우리 로그가 아니라 구글 화면에 뜬다.
    """

    session_secret: SecretStr | None = None
    """세션 쪽지 서명 키.

    🔴 **없으면 로그인 자체가 안 되게** 한다 (`apps/api/auth.py`). 빈 키로 서명하면
    누구나 위조하므로, 조용히 도는 것보다 안 뜨는 것이 낫다 (절대 규칙 #8).
    """

    admin_emails: str | None = None
    """첫 관리자 이메일 — 쉼표로 여럿 (사용자 확정 2026-08-30 "설정에 이메일 고정").

    🔴 **가입 승인을 줄 사람이 최소 한 명은 미리 있어야 한다.** 여기 적힌 계정은
    처음 로그인할 때 `admin` 으로 만들어진다.

    ⚠️ **이미 있는 계정의 등급을 끌어올리지는 않는다** — 설정 파일 한 줄로 남의 등급이
    바뀌면 관리자 화면의 기록(`approved_by`)과 사실이 갈린다.
    """

    @field_validator(*_OPTIONAL_FIELDS, mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """빈 문자열을 `None` 으로 정규화한다.

        Args:
            value: 환경변수에서 온 원본 값.

        Returns:
            공백뿐이면 None, 그 외에는 원본.

        Note:
            **이것이 없으면 fail-fast 가 무력화된다.** `.env` 에 `TOSS_CLIENT_SECRET=`
            처럼 자리만 잡아 둔 키는 빈 문자열이 되고, 빈 문자열은 `None` 이 아니므로
            "설정됨"으로 판정된다. 그러면 미수령 자격증명으로 `TossCredentials` 가
            만들어져 브로커 호출 시점에야 실패한다 (spec §7 위반).
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _blank_log_level_uses_default(cls, value: object) -> object:
        """빈 `LOG_LEVEL` 은 기본값으로 되돌린다."""
        if isinstance(value, str) and not value.strip():
            return "INFO"
        return value

    @field_validator("database_url", "redis_url", mode="after")
    @classmethod
    def _reject_blank_required(cls, value: str, info: ValidationInfo) -> str:
        """필수 접속 URL 이 빈 값이면 거부한다.

        Args:
            value: 검증 대상 값.
            info: 필드 정보.

        Returns:
            검증을 통과한 값.

        Raises:
            ValueError: 값이 공백뿐인 경우.

        Note:
            `str` 타입은 빈 문자열도 유효한 값으로 통과시킨다. `DATABASE_URL=` 로 두면
            빈 URL 로 접속을 시도하다 런타임에 실패하는데, 그때는 원인이 멀어져 있다.
            현재 `.env.live` 의 인프라 항목이 정확히 이 상태다.
        """
        if not value.strip():
            raise ValueError(
                f"{info.field_name} 이 비어 있다 — 값을 채우거나 키를 지운다 (spec §7)"
            )
        return value

    @model_validator(mode="after")
    def _reject_live_secrets_outside_live(self) -> Self:
        """비-live 환경에 live 자격증명이 있으면 기동을 거부한다 (spec §12.4).

        Returns:
            검증을 통과한 자기 자신.

        Raises:
            LiveSecretAccessError: `app_env != live` 인데 live 전용 키가 설정된 경우.

        Note:
            **이것이 격리의 1차 방어선이다.** 접근 시점 검사만 두면 dev 프로세스가
            `.env.live` 를 로드한 채로 "아직 안 쓰니까 괜찮은" 상태로 돌아간다. 그 상태는
            언젠가 누가 자격증명을 읽는 코드를 추가하는 순간 사고가 된다.

            그래서 **값의 존재 자체**를 거부한다. 개발자가 셸에 `TOSS_CLIENT_ID` 를
            export 해 둔 경우도 잡히는데, 그것도 §12.4 네임스페이스 분리 위반이므로
            의도된 동작이다.
        """
        if self.app_env is AppEnv.LIVE:
            return self

        present = [f for f in LIVE_ONLY_FIELDS if getattr(self, f) is not None]
        if present:
            raise LiveSecretAccessError(
                f"APP_ENV={self.app_env.value} 인데 live 전용 키가 설정되어 있다: "
                f"{', '.join(present)}. live 자격증명은 .env.live 에만 두고 "
                f"APP_ENV=live 프로세스에서만 로드한다 (spec §12.4)."
            )
        return self

    @property
    def is_live(self) -> bool:
        """Live 환경인가."""
        return self.app_env is AppEnv.LIVE

    @property
    def toss_proxy(self) -> tuple[str, SecretStr] | None:
        """토스 프록시 `(url, token)` — 둘 다 있을 때만. 없으면 None(직접 호출).

        Returns:
            `(base_url, token)` 또는 None.

        Raises:
            ConfigurationError: 하나만 있는 경우 — 반쯤 켜진 채 조용히 직접 호출로
                떨어지면 토큰이 둘이 된다(규칙 #8).
        """
        url, token = self.toss_proxy_url, self.toss_proxy_token
        if url is None and token is None:
            return None
        if url is None or token is None:
            missing = "TOSS_PROXY_URL" if url is None else "TOSS_PROXY_TOKEN"
            raise ConfigurationError(
                f"토스 프록시 설정이 반쪽이다 — {missing} 이 없다. 둘 다 두거나 둘 다 지운다"
            )
        return url, token

    @property
    def toss_market_data_credentials(self) -> tuple[SecretStr, SecretStr]:
        """**조회 전용** 토스 자격증명 (client_id, client_secret).

        Returns:
            `(client_id, client_secret)`.

        Raises:
            ConfigurationError: 둘 중 하나라도 없는 경우.

        Note:
            ## 왜 live 환경을 요구하지 않는가 — 범위를 좁혀 격리를 지킨다

            §12.4 격리의 목적은 **비-live 프로세스가 실주문을 낼 수 없게** 하는 것이다.
            그 목적은 자격증명이 아니라 **주문 경로**에서 지켜진다:

            | 방어선 | 상태 |
            |---|---|
            | `OrderGateway` 가 어댑터 획득을 독점 | Phase 0~1 에는 **아무것도 반환하지 않는다** |
            | `TossAdapter` 의 주문 메서드 | 항상 `OrderPathNotAvailableError` |

            백테스트용 **과거 캔들 수집**은 조회이며, 그것 때문에 프로세스 전체를
            `APP_ENV=live` 로 띄우는 편이 오히려 위험하다 — live 는 실주문을 전제한
            환경이고 `.env.live` 에는 DB 접속 정보도 없다.

            ⚠️ **그래서 이 프로퍼티는 인증서를 주지 않는다.** 주문에 필요한 묶음은
            여전히 `toss_credentials` 이고 그쪽은 live 를 요구한다. 조회에 필요한
            최소치만 여기서 열린다.

            ⛔ **이 프로퍼티를 주문 경로에서 쓰지 않는다.** 쓰려는 코드가 생기면 그것은
            `OrderGateway` 를 우회하는 것이며 절대 규칙 #0 위반이다.
        """
        # 조회 네임스페이스를 먼저 본다. live 환경에서는 주문용 자격증명으로 대체할 수
        # 있게 두는데, live 에는 어차피 둘 다 있고 키를 두 번 적게 하는 것이 오타를 늘리기
        # 때문이다. 비-live 는 `TOSS_*` 를 애초에 가질 수 없으므로 이 대체가 격리를
        # 약화시키지 않는다 (`_reject_live_secrets_outside_live` 가 먼저 막는다).
        client_id = self.toss_marketdata_client_id or (
            self.toss_client_id if self.is_live else None
        )
        client_secret = self.toss_marketdata_client_secret or (
            self.toss_client_secret if self.is_live else None
        )
        if client_id is None or client_secret is None:
            missing = [
                name
                for name, value in (
                    ("TOSS_MARKETDATA_CLIENT_ID", client_id),
                    ("TOSS_MARKETDATA_CLIENT_SECRET", client_secret),
                )
                if value is None
            ]
            raise ConfigurationError(
                f"토스 조회 자격증명이 없다. 누락: {', '.join(missing)}.\n"
                "OAuth2 client_credentials 에는 이 둘이면 충분하다 (인증서 불필요 — "
                "docs/platform/toss_api_notes.md §1).\n"
                "⚠️ 비-live 환경에는 `TOSS_CLIENT_*` 를 두면 기동이 거부된다 (§12.4). "
                "조회용은 반드시 `TOSS_MARKETDATA_*` 이름으로 넣는다"
            )
        return client_id, client_secret

    @property
    def toss_credentials(self) -> TossCredentials:
        """토스 자격증명을 얻는다.

        Returns:
            자격증명 묶음.

        Raises:
            LiveSecretAccessError: `app_env != live` 인 경우 — **환경에 값이 있든 없든**
                항상 거부한다 (격리의 2차 방어선).
            ConfigurationError: live 환경인데 자격증명이 불완전한 경우. 부분 설정으로
                기동해 런타임에 실패하는 것보다 즉시 중단이 낫다 (spec §7).

        Note:
            Phase 0~1 에서는 이 프로퍼티를 부르는 코드가 없다. 토스 사용 시점은 P2-9 이며
            그때까지 `.env.live` 는 **보관만** 한다 (plan P-4).
        """
        if not self.is_live:
            raise LiveSecretAccessError(
                f"APP_ENV={self.app_env.value} 에서는 토스 자격증명에 접근할 수 없다 "
                f"(spec §12.4). live 브로커 키는 live 환경에서만 로드 가능하다."
            )
        # 개별 변수로 꺼내 좁힌다. `assert` 는 `python -O` 에서 제거되므로
        # 타입 좁히기를 그것에 의존하지 않는다.
        client_id = self.toss_client_id
        client_secret = self.toss_client_secret
        cert_path = self.toss_cert_path
        cert_key_path = self.toss_cert_key_path
        if client_id is None or client_secret is None or cert_path is None or cert_key_path is None:
            missing = [f for f in LIVE_ONLY_FIELDS if getattr(self, f) is None]
            raise ConfigurationError(
                f"live 환경인데 토스 자격증명이 불완전하다. 누락: {', '.join(missing)}. "
                "X-1 트랙에서 Client Secret·mTLS 인증서 수령이 완료되어야 P2-9 를 시작할 수 있다."
            )
        return TossCredentials(
            client_id=client_id,
            client_secret=client_secret,
            cert_path=cert_path,
            cert_key_path=cert_key_path,
        )


def load_settings() -> Settings:
    """환경변수에서 설정을 읽고 부팅 전 1회 검증한다 (spec §7 fail-fast).

    Returns:
        검증된 설정.

    Raises:
        ConfigurationError: 필수 키 누락, 값 형식 오류, live 시크릿 격리 위반.

    Note:
        pydantic 의 `ValidationError` 를 그대로 올리지 않고 감싸는 이유는 메시지다.
        기동 실패 시 개발자가 봐야 하는 것은 "어떤 키가 없는지"와 "어디에 넣어야
        하는지"이며, 스택트레이스가 아니다 (spec §7 "명확한 메시지와 함께 즉시 종료").
    """
    try:
        return Settings()  # pyright: ignore[reportCallIssue]  (환경변수에서 채워진다)
    except LiveSecretAccessError:
        raise
    except Exception as exc:
        raise ConfigurationError(
            f"설정 로딩 실패 — 기동을 중단한다.\n{exc}\n\n"
            "APP_ENV / DATABASE_URL / REDIS_URL 은 필수다. "
            "`.env.example` 을 참고해 `.env.{dev,paper,live}` 를 채운다."
        ) from exc
