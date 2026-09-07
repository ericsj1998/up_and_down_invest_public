"""겹칩선은 **차트 전부**가 같은 코드로 그린다 (2026-08-30).

사용자: *"이평선은 오히려 있어야 할 것 같은데? … 공유할 수 있는 부분들까지도
하드코딩해서 사용할 필요는 없다는 거지. 재사용성 측면에서 말야."*

## 무엇이 잘못돼 있었나

이평선·ADX 를 그리는 코드가 `apps/api/walkforward.py` **안에만** 있었다. 그래서
RUN 상세는 이평선을 그리고 **차트 주문은 못 그렸다** — 같은 봉을 보는 두 화면이 다른
것을 보여 줬고, 화면에는 그 이유가 어디에도 안 적혀 있었다 (규칙 #8).

## 🔴 경계는 *"무엇을 알아야 그릴 수 있나"* 다

    공용    `FrameView` 만 있으면 되는 것        ma_layer · adx_overlay
    API층   플레이북·세션·게이트를 알아야 하는 것  slope · vol · stance · adx_gates

⚠️ 후자를 억지로 공용으로 끌어내리면 그 모듈이 **판을 알아야** 하고, 그러면 판이 없는
분석 화면이 아무것도 못 그린다. 다 합치는 것이 재사용이 아니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from updown.orchestration.inspection.overlays import (
    ADX_OVERLAY_FLAG,
    DEFAULT_MA_PERIOD,
    MA_OVERLAY_FLAG,
    adx_overlay,
    ma_layer,
)

SRC = Path("src/updown")
API = SRC / "apps" / "api"
SHARED = SRC / "orchestration" / "inspection" / "overlays.py"


class TestOnePlaceDrawsThem:
    def test_the_api_layer_no_longer_defines_them(self) -> None:
        """⛔ 정의가 API 층에 남아 있으면 한쪽 화면만 고쳐진다."""
        source = (API / "walkforward.py").read_text(encoding="utf-8")
        assert "def _ma_layer(" not in source
        assert "def adx_layer(" not in source

    @pytest.mark.parametrize("name", ["walkforward.py", "analysis.py"])
    def test_both_charts_import_the_shared_ones(self, name: str) -> None:
        """🔴 RUN 상세와 차트 주문이 **같은 함수**를 부른다."""
        source = (API / name).read_text(encoding="utf-8")
        assert "from updown.orchestration.inspection.overlays import" in source, name
        assert "ma_layer(" in source, name
        assert "adx_overlay(" in source, name

    def test_the_shared_module_does_not_know_about_playbooks(self) -> None:
        """⚠️ 판을 알면 판 없는 화면이 못 쓴다 — 그것이 이 분리의 요점이다.

        ⚠️ **말이 아니라 `import` 를 본다.** 처음에는 원문에서 단어를 찾았는데 *자기
        독스트링*에 걸렸다 (`config/playbooks.yml` 을 설명하는 문장). 설명글을 코드로
        오해하는 시험은 아무것도 안 지킨다 — 이 실수를 오늘만 두 번 했다.
        """
        lines = [
            line
            for line in SHARED.read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
        ]
        for banned in ("playbook", "walkforward", "session", "decision"):
            guilty = [line for line in lines if banned in line.lower()]
            assert guilty == [], f"공용 모듈이 {banned} 를 import 한다: {guilty}"

    def test_what_stays_in_the_api_layer_stays(self) -> None:
        """⛔ 다 합치는 것이 재사용이 아니다 — 판을 알아야 하는 것은 위층에 남는다."""
        source = (API / "walkforward.py").read_text(encoding="utf-8")
        for kept in ("def slope_layer(", "def vol_layer(", "def stance_layer("):
            assert kept in source, kept


class TestTheyDrawWithoutAPlaybook:
    """분석 화면에는 판이 없다 — 그래도 그려져야 한다."""

    def test_the_default_period_matches_what_actually_runs(self) -> None:
        """🔴 **지어낸 값이 아니다** — 도는 판들이 전부 이 값을 쓴다."""
        book = Path("config/playbooks.yml").read_text(encoding="utf-8")
        periods = {
            line.split(":")[1].split("#")[0].strip()
            for line in book.splitlines()
            if line.strip().startswith("trail_ma:") and "null" not in line
        }
        if not periods:
            pytest.skip("trail_ma 를 선언한 플레이북이 없는 설정 (공개본 예시)")
        assert periods == {str(DEFAULT_MA_PERIOD)}, (
            f"설정의 trail_ma 가 {periods} 인데 기본값은 {DEFAULT_MA_PERIOD} 다"
        )

    def test_flags_are_the_names_the_screen_looks_for(self) -> None:
        """⚠️ 이름이 갈리면 층은 실려 오는데 화면이 못 찾는다 — 조용히 빈 칸이 된다."""
        chart = Path("web/src/Chart.tsx").read_text(encoding="utf-8")
        assert MA_OVERLAY_FLAG in chart
        assert ADX_OVERLAY_FLAG in chart

    def test_they_are_callable_with_a_view_alone(self) -> None:
        """서명이 `FrameView` 하나면 어느 화면에서든 부를 수 있다."""
        import inspect

        for fn in (ma_layer, adx_overlay):
            names = list(inspect.signature(fn).parameters)
            assert names[0] == "view", fn.__name__
            rest = names[1:]
            assert all(
                inspect.signature(fn).parameters[one].default is not inspect.Parameter.empty
                for one in rest
            ), f"{fn.__name__} 이 판 없이는 못 불린다"
