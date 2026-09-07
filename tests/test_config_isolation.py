"""live 시크릿 격리 증명 (P0-5-3 · P0-5-6 · spec §12.4, §8, §7).

DoD 1 — **dev 환경에서 live 키 로드 시도가 예외로 실패함**을 증명한다.
격리는 두 층이다: 값의 **존재** 거부(기동 차단) + 값의 **접근** 거부(프로퍼티).
"""

import pytest

from updown.common.config import (
    AppEnv,
    ConfigurationError,
    LiveSecretAccessError,
    Settings,
    load_settings,
)

BASE_ENV = {
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5433/db",
    "REDIS_URL": "redis://localhost:6380/0",
}

LIVE_SECRETS = {
    "TOSS_CLIENT_ID": "tsck_live_dummy",
    "TOSS_CLIENT_SECRET": "secret_dummy",
    "TOSS_CERT_PATH": "/certs/toss.pem",
    "TOSS_CERT_KEY_PATH": "/certs/toss.key",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트 프로세스의 실제 환경변수가 결과에 새지 않게 한다."""
    for key in ("APP_ENV", "LOG_LEVEL", "DART_API_KEY", "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key in (*BASE_ENV, *LIVE_SECRETS):
        monkeypatch.delenv(key, raising=False)


def _set(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# 1. fail-fast (DoD 3)
# ---------------------------------------------------------------------------


def test_missing_app_env_aborts_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_ENV 는 기본값이 없다 — 없으면 기동하지 않는다 (spec §12.4)."""
    _set(monkeypatch, **BASE_ENV)
    with pytest.raises(ConfigurationError, match="APP_ENV"):
        load_settings()


def test_missing_database_url_aborts_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """DoD 3 — 필수 키 누락 시 명확한 메시지와 함께 즉시 종료."""
    _set(monkeypatch, APP_ENV="dev", REDIS_URL=BASE_ENV["REDIS_URL"])
    with pytest.raises(ConfigurationError) as exc:
        load_settings()
    assert "DATABASE_URL" in str(exc.value)
    assert ".env" in str(exc.value), "어디를 고쳐야 하는지가 메시지에 있어야 한다"


def test_invalid_app_env_aborts_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """오타가 조용히 통과하지 않는다."""
    _set(monkeypatch, APP_ENV="prod", **BASE_ENV)
    with pytest.raises(ConfigurationError):
        load_settings()


# ---------------------------------------------------------------------------
# 2. live 시크릿 격리 — 1차 방어선: 존재 자체를 거부 (DoD 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_name", ["dev", "paper"])
def test_live_secrets_present_in_non_live_env_abort_boot(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    """DoD 1 — dev/paper 프로세스에 live 키가 있으면 **기동 자체가 실패**한다.

    이것이 없으면 `.env.live` 를 잘못 로드한 dev 프로세스가 "아직 안 쓰니까
    괜찮은" 상태로 돌아간다. 그 상태는 누가 자격증명을 읽는 코드를 추가하는
    순간 사고가 된다 (spec §12.4).
    """
    _set(monkeypatch, APP_ENV=env_name, **BASE_ENV, **LIVE_SECRETS)
    with pytest.raises(LiveSecretAccessError) as exc:
        load_settings()
    assert "toss_client_id" in str(exc.value)
    assert ".env.live" in str(exc.value)


def test_partial_live_secret_in_dev_also_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    """키 하나만 새어 들어와도 거부한다 — 부분 유출도 격리 위반이다."""
    _set(monkeypatch, APP_ENV="dev", **BASE_ENV, TOSS_CLIENT_ID="tsck_live_dummy")
    with pytest.raises(LiveSecretAccessError):
        load_settings()


def test_dev_boots_cleanly_without_live_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """정상 경로 — live 키가 없으면 dev 는 문제없이 기동한다."""
    _set(monkeypatch, APP_ENV="dev", **BASE_ENV, UPBIT_ACCESS_KEY="ak", UPBIT_SECRET_KEY="sk")
    settings = load_settings()
    assert settings.app_env is AppEnv.DEV
    assert settings.is_live is False


# ---------------------------------------------------------------------------
# 3. live 시크릿 격리 — 2차 방어선: 접근을 거부 (DoD 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_name", ["dev", "paper"])
def test_toss_credentials_unreachable_outside_live(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    """`.env.live` 존재 여부와 무관하게 dev 는 live 값을 읽지 못한다 (P0-5-6).

    1차 방어선을 어떤 이유로 통과했더라도 접근 시점에서 다시 막힌다.
    """
    _set(monkeypatch, APP_ENV=env_name, **BASE_ENV)
    settings = load_settings()
    with pytest.raises(LiveSecretAccessError, match="접근할 수 없다"):
        _ = settings.toss_credentials


def test_live_env_can_read_toss_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """live 환경에서는 정상적으로 읽힌다 — 격리가 기능을 막지는 않는다."""
    _set(monkeypatch, APP_ENV="live", **BASE_ENV, **LIVE_SECRETS)
    creds = load_settings().toss_credentials
    assert creds.client_id.get_secret_value() == "tsck_live_dummy"


def test_live_env_with_incomplete_credentials_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    """live 인데 자격증명이 반쪽이면 즉시 중단한다 (spec §7).

    Note:
        현실 상황이다 — Client ID 는 수령했고 Secret·인증서는 미수령이다 (X-1 ⓪).
        이 상태로 P2-9 를 시작하면 런타임에 실패하므로 부팅에서 잡는다.
    """
    _set(monkeypatch, APP_ENV="live", **BASE_ENV, TOSS_CLIENT_ID="tsck_live_dummy")
    settings = load_settings()
    with pytest.raises(ConfigurationError, match="불완전"):
        _ = settings.toss_credentials


# ---------------------------------------------------------------------------
# 4. 시크릿 평문 노출 금지 (DoD 5)
# ---------------------------------------------------------------------------


def test_secrets_never_appear_in_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    """DoD 5 — 로그에 시크릿 평문이 없어야 한다 (spec §8).

    구조화 로깅은 객체를 `repr` 로 직렬화하는 경우가 많아, 여기서 새면 로그로 새 나간다.
    """
    _set(
        monkeypatch,
        APP_ENV="dev",
        **BASE_ENV,
        UPBIT_ACCESS_KEY="LEAK_ACCESS",
        UPBIT_SECRET_KEY="LEAK_SECRET",
        DART_API_KEY="LEAK_DART",
    )
    settings = load_settings()
    for rendered in (repr(settings), str(settings), repr(settings.model_dump())):
        for leaked in ("LEAK_ACCESS", "LEAK_SECRET", "LEAK_DART"):
            assert leaked not in rendered, f"{leaked} 가 {rendered[:80]}... 에 노출됐다"


def test_toss_credentials_repr_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, APP_ENV="live", **BASE_ENV, **LIVE_SECRETS)
    creds = load_settings().toss_credentials
    assert "tsck_live_dummy" not in repr(creds)
    assert "secret_dummy" not in repr(creds)


# ---------------------------------------------------------------------------
# 5. 빈 값 = 미설정 — fail-fast 무력화 방지
# ---------------------------------------------------------------------------


def test_blank_live_secret_counts_as_unset_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.env.live` 의 현재 상태를 재현한다 — Client Secret·인증서 미수령 (X-1 ⓪).

    빈 문자열을 "설정됨"으로 보면 미수령 자격증명으로 `TossCredentials` 가 만들어져
    브로커 호출 시점에야 실패한다. 부팅 시점에 잡혀야 한다 (spec §7).
    """
    _set(
        monkeypatch,
        APP_ENV="live",
        **BASE_ENV,
        TOSS_CLIENT_ID="tsck_live_dummy",
        TOSS_CLIENT_SECRET="",
        TOSS_CERT_PATH="",
        TOSS_CERT_KEY_PATH="",
    )
    settings = load_settings()
    assert settings.toss_client_secret is None, "빈 문자열은 미설정으로 취급해야 한다"
    with pytest.raises(ConfigurationError, match="불완전"):
        _ = settings.toss_credentials


def test_blank_live_secret_in_dev_does_not_false_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """반대 방향 — dev 에 빈 TOSS 키가 있어도 격리 위반으로 오판하지 않는다.

    `.env.example` 을 복사해 만든 `.env.dev` 에는 빈 TOSS 키가 남아 있을 수 있다.
    그것을 위반으로 잡으면 정상 개발 환경이 기동하지 못한다.
    """
    _set(monkeypatch, APP_ENV="dev", **BASE_ENV, TOSS_CLIENT_ID="", TOSS_CLIENT_SECRET="   ")
    settings = load_settings()
    assert settings.app_env is AppEnv.DEV


def test_blank_required_url_aborts_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 `DATABASE_URL` 이 필수 검사를 통과하면 안 된다.

    `str` 타입은 빈 문자열을 유효한 값으로 받으므로 별도 검증이 필요하다.
    현재 `.env.live` 의 인프라 항목이 이 상태다.
    """
    _set(monkeypatch, APP_ENV="dev", DATABASE_URL="", REDIS_URL=BASE_ENV["REDIS_URL"])
    with pytest.raises(ConfigurationError, match="database_url"):
        load_settings()


def test_blank_log_level_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본값이 있는 필드는 빈 값에서 기본값으로 돌아간다."""
    _set(monkeypatch, APP_ENV="dev", **BASE_ENV, LOG_LEVEL="")
    assert load_settings().log_level == "INFO"


def test_settings_requires_explicit_construction_values() -> None:
    """`Settings()` 를 환경 없이 부르면 실패한다 — 암묵적 기본값이 없음을 고정."""
    with pytest.raises(Exception, match=r"app_env|APP_ENV|validation"):
        Settings(app_env=None)  # type: ignore[arg-type]  # pyright: ignore[reportCallIssue]
