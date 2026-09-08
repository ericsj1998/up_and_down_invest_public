"""실거래 관문 — 사실 → 판정 (2026-09-04). 낡은 상수 목록을 런타임 계산으로 바꿨다."""

from __future__ import annotations

from dataclasses import replace

from updown.apps.api.gates import LIVE_SAMPLE_MIN, Facts, Gate, evaluate

BASE = Facts(
    app_env="dev",
    live_adapter_exists=False,
    live_branch_blocked=True,
    live_keys_readable=False,
    breaker_wired=True,
    reconcile_age_s=120.0,
    slippage_source={"GATE": "assumed", "BINANCE": "measured"},
    live_closed_trades=4,
    calibration_rows=277,
    mdd_limit_configured=False,
)


def _by_title(rows: list[Gate]) -> dict[str, Gate]:
    return {g.title: g for g in rows}


def test_current_state_blocks_and_passes_the_right_gates() -> None:
    g = _by_title(evaluate(BASE))
    assert not g["라이브 주문 어댑터"].done and g["라이브 주문 어댑터"].blocking
    assert not g["OrderGateway LIVE 분기"].done  # 차단돼 있으면 "열림" 관문은 미통과
    assert g["브레이커(일일 손실 한도) 라이브 배선"].done  # T22 — 옛 화면은 미완이라 했다
    assert g["거래소 대조(reconcile)가 돈다"].done
    assert not g["Gate 슬리피지 실측"].done and g["Binance 슬리피지 실측"].done
    assert g["페이퍼 교정 원장 (T185)"].done
    assert not g[f"테스트넷 청산 표본 {LIVE_SAMPLE_MIN}건"].done
    assert "4/30" in g[f"테스트넷 청산 표본 {LIVE_SAMPLE_MIN}건"].detail
    assert not g["실거래 개시 결정 (G1 · 사람)"].done


def test_facts_move_the_verdict() -> None:
    later = replace(
        BASE,
        live_closed_trades=31,
        slippage_source={"GATE": "measured", "BINANCE": "measured"},
        mdd_limit_configured=True,
        reconcile_age_s=None,
    )
    g = _by_title(evaluate(later))
    assert g[f"테스트넷 청산 표본 {LIVE_SAMPLE_MIN}건"].done
    assert g["Gate 슬리피지 실측"].done
    assert g["MDD 허용치 확정 (D1-5)"].done
    assert not g["거래소 대조(reconcile)가 돈다"].done, "대조 파일이 없으면 통과가 아니다"


def test_decision_file_opens_human_gates_and_only_those() -> None:
    """사람이 쓴 결정 파일이 사람 관문(개시 결정 · MDD 면제 · 표본 근거)을 연다.

    코드 관문은 그대로다.
    """
    decided = replace(
        BASE,
        decision={
            "decided_at": "2026-09-05",
            "by": "u@example.com",
            "mode": "small_live",
            "exchange": "GATE",
            "symbols": 6,
            "capital_usdt": 300,
            "mdd_waived_reason": "펀드 사이징이 대신한다",
            "testnet_evidence": "dev 206건",
        },
    )
    g = _by_title(evaluate(decided))
    assert g["실거래 개시 결정 (G1 · 사람)"].done
    mdd = g["MDD 허용치 확정 (D1-5)"]
    assert mdd.done and "두지 않기로" in mdd.detail
    assert g[f"테스트넷 청산 표본 {LIVE_SAMPLE_MIN}건"].done
    # 코드·설정 관문은 결정 파일로 열리지 않는다
    assert not g["라이브 주문 어댑터"].done
    assert not g["Gate 슬리피지 실측"].done


def test_half_written_decision_does_not_count() -> None:
    """날짜만 있고 누가 정했는지 없으면 결정이 아니다."""
    g = _by_title(evaluate(replace(BASE, decision={"decided_at": "2026-09-05"})))
    assert not g["실거래 개시 결정 (G1 · 사람)"].done
    assert not g["MDD 허용치 확정 (D1-5)"].done


def test_human_gate_is_never_computed_open() -> None:
    """어떤 **사실** 조합도 사람의 결정을 대신 통과시키지 않는다 — 결정 파일이 없으면 닫혀 있다."""
    everything = replace(
        BASE,
        live_adapter_exists=True,
        live_branch_blocked=False,
        live_keys_readable=True,
        live_closed_trades=999,
        slippage_source={"GATE": "measured", "BINANCE": "measured"},
        mdd_limit_configured=True,
    )
    g = _by_title(evaluate(everything))
    assert not g["실거래 개시 결정 (G1 · 사람)"].done and g["실거래 개시 결정 (G1 · 사람)"].blocking
