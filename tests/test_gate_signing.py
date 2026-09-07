"""Gate.io v4 요청 서명 (`marketdata/gate/signing.py`).

## 왜 이 테스트가 중요한가

서명이 틀리면 **401 만 돌아온다.** 문자열 하나가 어긋난 것과 키가 잘못된 것과 IP
화이트리스트 불일치가 전부 같은 응답으로 보인다 — 그래서 눈으로는 못 가른다.

⇒ Gate 문서에 실린 **정답 벡터**로 잠근다. `key`/`secret` 로 서명한 결과가 문서와
  글자까지 같아야 한다. 이게 맞으면 401 이 나올 때 서명은 용의자에서 빠진다.
"""

import pytest

from updown.marketdata.gate.signing import (
    API_PREFIX,
    EMPTY_BODY_SHA512,
    auth_headers,
    body_hash,
    query_string,
    sign,
    signature_payload,
)
from updown.marketdata.gate.trade_client import IDEMPOTENCY_PREFIX, TEXT_LIMIT, gate_text

# 문서 §API Signature string generation 의 예제. 키와 시크릿이 문자 그대로 이것이다.
KEY = "key"
SECRET = "secret"
STAMP = 1541993715

GET_EXPECTED = (
    "55f84ea195d6fe57ce62464daaa7c3c02fa9d1dde954e4c898289c9a2407a3d6"
    "fb3faf24deff16790d726b66ac9f74526668b13bd01029199cc4fcc522418b8a"
)
POST_BODY = '{"contract":"BTC_USD","type":"limit","size":100,"price":6800,"time_in_force":"gtc"}'
POST_BODY_SHA512 = (
    "ad3c169203dc3026558f01b4df307641fa1fa361f086b2306658886d5708767b"
    "1854797c68d9e62fef2f991645aa82673622ebf417e091d0bd22bafe5d956cca"
)
POST_EXPECTED = (
    "eae42da914a590ddf727473aff25fc87d50b64783941061f47a3fdb92742541f"
    "c4c2c14017581b4199a1418d54471c269c03a38d788d802e2c306c37636389f0"
)


class TestDocumentVectors:
    """🔴 문서의 정답과 글자까지 같아야 한다."""

    def test_the_get_example_matches(self) -> None:
        """`GET /futures/orders?contract=BTC_USD&status=finished&limit=50`."""
        params = {"contract": "BTC_USD", "status": "finished", "limit": "50"}
        payload = signature_payload("GET", "/futures/orders", params, "", STAMP)
        assert sign(SECRET, payload) == GET_EXPECTED

    def test_the_post_example_matches(self) -> None:
        """`POST /futures/orders` + JSON 본문. 쿼리는 빈 문자열이다."""
        payload = signature_payload("POST", "/futures/orders", None, POST_BODY, STAMP)
        assert sign(SECRET, payload) == POST_EXPECTED

    def test_the_documented_body_hash_matches(self) -> None:
        assert body_hash(POST_BODY) == POST_BODY_SHA512

    def test_the_empty_body_hash_matches(self) -> None:
        """⛔ 본문이 없으면 **빈 문자열의 SHA512** 다. 빈 해시나 `""` 가 아니다."""
        assert body_hash("") == EMPTY_BODY_SHA512


class TestThePathPrefix:
    """🔴 서명 경로에는 `/api/v4` 가 들어간다 — 클라이언트가 보내는 경로와 다르다."""

    def test_the_prefix_is_added(self) -> None:
        payload = signature_payload("GET", "/futures/usdt/accounts", None, "", STAMP)
        assert payload.split("\n")[1] == f"{API_PREFIX}/futures/usdt/accounts"

    def test_it_is_not_doubled(self) -> None:
        """이미 붙어 온 경로에 또 붙이면 `/api/v4/api/v4/...` 가 된다."""
        payload = signature_payload("GET", "/api/v4/futures/usdt/accounts", None, "", STAMP)
        assert payload.split("\n")[1] == f"{API_PREFIX}/futures/usdt/accounts"

    def test_a_wrong_prefix_changes_the_signature(self) -> None:
        """⚠️ 접두를 빼면 서명이 달라진다 — 증상은 401 뿐이다.

        이 테스트가 지키는 것은 값이 아니라 **접두가 서명에 실제로 영향을 준다**는
        사실이다. 영향이 없으면 위 두 테스트가 무의미해진다.
        """
        with_prefix = signature_payload("GET", "/futures/orders", None, "", STAMP)
        without = "\n".join(["GET", "/futures/orders", "", EMPTY_BODY_SHA512, str(STAMP)])
        assert sign(SECRET, with_prefix) != sign(SECRET, without)


class TestQueryString:
    def test_no_params_is_an_empty_string(self) -> None:
        assert query_string(None) == ""
        assert query_string({}) == ""

    def test_insertion_order_is_kept(self) -> None:
        """🔴 정렬하지 않는다 — Gate 가 "URL 에 붙은 순서 그대로" 를 요구한다."""
        assert query_string({"b": "2", "a": "1"}) == "b=2&a=1"

    def test_sorting_would_break_the_signature(self) -> None:
        """⚠️ 순서가 서명에 영향을 준다는 것을 잠근다."""
        one = signature_payload("GET", "/x", {"b": "2", "a": "1"}, "", STAMP)
        two = signature_payload("GET", "/x", {"a": "1", "b": "2"}, "", STAMP)
        assert sign(SECRET, one) != sign(SECRET, two)

    def test_contract_names_survive(self) -> None:
        """`BTC_USDT` 의 밑줄이 `%5F` 로 바뀌면 서명이 깨진다."""
        assert query_string({"contract": "BTC_USDT"}) == "contract=BTC_USDT"


class TestHeaders:
    def test_it_returns_the_three_auth_headers(self) -> None:
        headers = auth_headers(
            KEY,
            SECRET,
            "GET",
            "/futures/orders",
            params={"contract": "BTC_USD", "status": "finished", "limit": "50"},
            timestamp=STAMP,
        )
        assert headers["KEY"] == KEY
        assert headers["Timestamp"] == str(STAMP)
        assert headers["SIGN"] == GET_EXPECTED

    def test_the_timestamp_is_injected(self) -> None:
        """🔴 현재시각을 직접 읽지 않는다 (절대 규칙 #5).

        서명 계산이 `datetime.now()` 를 읽으면 같은 입력에 다른 출력이 나오고,
        위의 문서 벡터 테스트가 애초에 불가능해진다.
        """
        first = auth_headers(KEY, SECRET, "GET", "/x", timestamp=1)
        second = auth_headers(KEY, SECRET, "GET", "/x", timestamp=1)
        assert first == second
        assert auth_headers(KEY, SECRET, "GET", "/x", timestamp=2)["SIGN"] != first["SIGN"]

    @pytest.mark.parametrize(("key", "secret"), [("", SECRET), (KEY, "")])
    def test_empty_credentials_raise(self, key: str, secret: str) -> None:
        """⛔ 빈 자격증명으로 조용히 서명하지 않는다.

        빈 시크릿으로 만든 서명도 **형태가 멀쩡하다** — 64바이트 hex 다. 그래서 401 이
        날 때까지 알 수 없고, 그때는 키가 틀렸는지 비었는지 못 가른다 (절대 규칙 #8).
        """
        with pytest.raises(ValueError, match="비었다"):
            auth_headers(key, secret, "GET", "/x", timestamp=STAMP)


class TestGateTextEncoding:
    """🔴 Gate `text` 는 콜론을 거부한다 — 멱등키 규격과 충돌한다.

    실측 2026-08-17: 주문에 `probe1a2b:entry:0` 을 넣으면

        400 INVALID_PARAM_VALUE — "text content includes illegal characters"

    그런데 멱등키 규격은 `{root}:{order_kind}:{leg_index}` 다 (§4.10 · §9). 콜론은
    **주문 단위를 가르는 구분자**이므로 도메인을 브로커 제약에 맞춰 휘지 않는다 —
    전송용으로만 바꾼다.
    """

    def test_colons_become_dashes(self) -> None:
        assert gate_text("abc123:entry:0") == "t-abc123-entry-0"

    def test_the_prefix_is_added_once(self) -> None:
        """`t-` 가 이미 있으면 또 붙이지 않는다 — Gate 가 접두를 요구한다."""
        assert gate_text("t-already") == "t-already"
        assert gate_text("plain").startswith("t-")

    def test_allowed_characters_survive(self) -> None:
        """영숫자·`-`·`_`·`.` 는 그대로 둔다."""
        assert gate_text("a1-b2_c3.d4") == "t-a1-b2_c3.d4"

    def test_it_is_deterministic(self) -> None:
        """🔴 **같은 키는 같은 text 여야 한다.**

        `find_order` 가 같은 변환을 거쳐야 재시도 전 조회(절대 규칙 #6)가 성립한다 —
        다르게 변환하면 이미 들어간 주문을 못 찾고 중복 주문이 난다.
        """
        assert gate_text("x:entry:0") == gate_text("x:entry:0")

    def test_different_legs_stay_different(self) -> None:
        """⛔ 변환이 레그를 뭉개면 멱등이 무너진다."""
        keys = {gate_text(f"root:take_profit:{leg}") for leg in (1, 2)}
        assert len(keys) == 2

    def test_non_ascii_is_replaced(self) -> None:
        """⚠️ 한글도 거부된다 — `isalnum()` 만 보면 통과하므로 ascii 여부도 본다."""
        assert gate_text("주문:0") == "t----0"

    def test_an_empty_key_raises(self) -> None:
        with pytest.raises(ValueError, match="비었다"):
            gate_text("")


class TestTextLengthLimit:
    """🔴 Gate `text` 는 **30자를 넘기면 거부한다** (실측 2026-08-18).

        400 INVALID_PARAM_VALUE — "text content length longer than 30"

    문서에 없어서 실제로 부딛혀 알았다 — 거래소 콘솔의 청산 키가 34자였고 `500` 이 났다.
    포지션을 닫으려는 버튼이 그것 때문에 동작하지 않았고, **그 사이 포지션은 손절도 없이
    거래소에 남아 있었다.**
    """

    def test_a_short_key_survives_intact(self) -> None:
        """짧으면 사람이 읽을 수 있는 형태 그대로다 — 접지 않는다."""
        assert gate_text("abc:entry:0") == "t-abc-entry-0"

    def test_the_limit_is_never_exceeded(self) -> None:
        """길이 상한을 **접두 포함**으로 지킨다."""
        long_key = "console-close-BTC_USDT-1786988000"
        assert len(gate_text(long_key)) <= TEXT_LIMIT

    def test_folding_is_deterministic(self) -> None:
        """⚠️ 같은 키는 늘 같은 text 다 — 아니면 재시도 조회가 깨진다 (절대 규칙 #6)."""
        long_key = "console-close-BTC_USDT-1786988000"
        assert gate_text(long_key) == gate_text(long_key)

    def test_different_keys_fold_differently(self) -> None:
        """⛔ **자르지 않는 이유가 이것이다.**

        앞 30자만 자르면 `...-1786988000` 과 `...-1786988001` 이 같은 text 가 되고,
        그러면 두 번째 주문이 첫 번째로 오인된다 — 멱등의 반대다.
        """
        a = gate_text("console-close-BTC_USDT-1786988000")
        b = gate_text("console-close-BTC_USDT-1786988001")
        assert a != b

    def test_a_folded_key_keeps_the_prefix(self) -> None:
        """접혀도 우리 주문이라는 표식은 남는다 — 거래소 화면에서 구별해야 한다."""
        folded = gate_text("console-close-BTC_USDT-1786988000")
        assert folded.startswith(IDEMPOTENCY_PREFIX)
