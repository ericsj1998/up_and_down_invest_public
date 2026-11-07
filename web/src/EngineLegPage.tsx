/**
 * 엔진 다리 상세 화면 — 판(RUN)이 없는 다리(나스닥)를 포지션 · 카드에서 눌러 여는 자리 (사용자 2026-11-07).
 *
 * RUN 상세는 세션 하나에 묶여 있어 엔진을 못 연다. 값은 펀드 목록의 `engine` 요약을 **열 때 한 번** 읽는다 —
 * 화면 폴링을 늘리지 않는다. 그 종목을 엔진이 안 돌리면 없는 화면으로 꾸미지 않고 그렇게 적는다.
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { type FundStatus, fundList } from "./api";
import { EngineLegPanel } from "./EngineLegPanel";

/** 이 종목을 엔진으로 돌리는 펀드 — 없으면 undefined. */
export function fundOfEngine(
  funds: readonly FundStatus[],
  symbol: string,
): FundStatus | undefined {
  return funds.find((f) => f.per_symbol?.[symbol]?.engine !== undefined);
}

export function EngineLegPage() {
  const symbol = useParams().symbol ?? "";
  const navigate = useNavigate();
  const [funds, setFunds] = useState<FundStatus[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    fundList()
      .then((got) => {
        if (alive) setFunds(got.funds);
      })
      .catch((exc: unknown) => {
        if (alive) setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      alive = false;
    };
  }, []);

  if (error) {
    return (
      <p role="alert" className="notice bad">
        펀드를 못 읽었다 — {error}
      </p>
    );
  }
  if (funds === null) return <p className="faint">불러오는 중…</p>;
  const fund = fundOfEngine(funds, symbol);
  const status = fund?.per_symbol[symbol]?.engine;
  if (!fund || !status) {
    return (
      <p className="notice">
        {symbol.replace("_USDT", "")} 은(는) 지금 펀드의 엔진 다리가 아니다 — 판
        화면이나 콘솔에서 본다.
      </p>
    );
  }
  return (
    <EngineLegPanel
      symbol={symbol}
      market={fund.market ?? ""}
      name={
        (fund.legs ?? []).find((leg) => leg.symbols.includes(symbol))?.name ??
        "엔진 다리"
      }
      status={status}
      closeLabel="뒤로"
      onClose={() => navigate(-1)}
    />
  );
}
