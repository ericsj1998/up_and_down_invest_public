/**
 * 이메일 성과 리포트 (T35) — **보내기 전에 본다.** 대시보드(`ReportDashboard`)의 한 카드다 (T220 2단계).
 *
 * 🔴 이것은 외부 전송이다. 집계가 평문으로 나가고 수신함에 영구히 남는다. 그래서
 * 순서가 고정이다: 미리보기 → 수신자 확인 → 보내기. 보내기 단추는 미리보기가 있어야
 * 살아나고, 누르면 확인 단계가 한 번 더 선다 — 오타 하나가 남의 수신함이다.
 *
 * ⭐ **판 고르기** (UX 점검 2026-09-05): 콘솔·판 화면에 따로 있던 "지금 보내기 · 이 판만" 을 여기로 모았다.
 *    전체(콘솔 이메일) 또는 판 하나(판 이메일) — 미리보기와 발송이 같은 `symbol` 을 쓴다.
 *
 * ⚠️ 권한: 서버가 정한다 (운용자 이상). 화면은 그 결과를 보여 줄 뿐이다.
 */

import { useEffect, useState } from "react";
import { reportPreview, reportSend, type ReportPreview } from "./api";
import { Button, Card, CardBody, Typography } from "./mt";

export function ReportMailCard({
  hours,
  symbols,
  runsCount,
}: {
  hours: number;
  /** 구간에 매매가 있던 종목들 — "이 판만" 선택지. */
  symbols: string[];
  /** 살아 있는 판 수 — 미리보기가 판마다 차트를 그리느라 걸리는 시간을 설명한다. */
  runsCount: number;
}) {
  const [to, setTo] = useState("");
  const [symbol, setSymbol] = useState("");
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>("");

  // 기간·범위가 바뀌면 미리보기는 옛것이다 — 지운다 (본 것과 보내는 것이 같아야 한다).
  useEffect(() => {
    setPreview(null);
    setConfirming(false);
  }, [hours, symbol]);

  const recipients = (to.trim() ? to : (preview?.default_to ?? []).join(","))
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);
  const scope = symbol ? `${symbol} 이 판만` : "전체";

  const load = async () => {
    setBusy(true);
    setResult("");
    setConfirming(false);
    try {
      setPreview(await reportPreview(hours, symbol || undefined));
    } catch (error) {
      setResult(`미리보기 실패: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    setBusy(true);
    try {
      const body = await reportSend({ hours, to: recipients, symbol: symbol || undefined });
      // 🔴 실패는 500 이 아니라 ok:false 다 — 이력(logs/report_sends.jsonl)에 남는다.
      setResult(
        body.ok ? `보냈다 (${scope} · 지난 ${hours}시간) → ${recipients.join(", ")}` : "보내지 못했다 — 발송 이력을 본다",
      );
    } catch (error) {
      setResult(`발송 실패: ${String(error)}`);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const input =
    "rounded-lg border border-blue-gray-200 bg-white px-3 py-2 text-sm text-blue-gray-900 outline-none focus:border-gray-900 dark:border-gray-700 dark:bg-gray-950 dark:text-white";

  return (
    <Card className="border border-blue-gray-100 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <CardBody className="flex flex-col gap-4 p-5">
        <div>
          <Typography variant="h6" color="blue-gray" className="dark:text-white">
            이메일로 보내기 · 지난 {hours}시간 · {scope}
          </Typography>
          <Typography variant="small" className="font-normal text-blue-gray-600 dark:text-blue-gray-300">
            매일 09:05 KST 에 지난 24시간(전체)이 기본 수신자에게 자동으로 간다. 여기서는 위 구간과 범위를 미리 보고,
            확인한 뒤 보낸다. <b>원장과 거래소를 나란히</b> 싣고, 갈리면 갈렸다고 쓴다.
          </Typography>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-blue-gray-600 dark:text-blue-gray-300">
            범위
            <select className={input} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              <option value="">전체 (거래소별·펀드 절 포함)</option>
              {symbols.map((name) => (
                <option key={name} value={name}>
                  {name} 이 판만
                </option>
              ))}
            </select>
          </label>
          <label className="flex min-w-[16rem] flex-1 flex-col gap-1 text-xs text-blue-gray-600 dark:text-blue-gray-300">
            수신자 (쉼표 · 비우면 기본 수신자)
            <input
              className={input}
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder={preview?.default_to.join(", ") || "기본 수신자"}
            />
          </label>
          <Button variant="outlined" color="blue-gray" size="sm" disabled={busy} onClick={load}>
            {busy && !preview ? "그리는 중…" : "미리보기"}
          </Button>
        </div>

        {busy && !preview ? (
          <Typography variant="small" className="font-normal text-blue-gray-500 dark:text-blue-gray-300" role="status">
            차트를 그리는 중{symbol ? "" : ` (살아 있는 판 ${runsCount}개 · 판마다 그림 하나)`} — 판이 많으면 수십 초.
          </Typography>
        ) : null}

        {preview ? (
          <>
            <div className="flex flex-wrap gap-2 text-xs">
              <span
                className={`rounded-full px-2.5 py-1 font-medium ${preview.diverged ? "bg-loss-wash text-loss" : "bg-gain-wash text-gain"}`}
              >
                {preview.diverged ? "🔴 원장·거래소 불일치" : "✅ 원장·거래소 일치"}
              </span>
              <span className="rounded-full bg-blue-gray-50 px-2.5 py-1 text-blue-gray-700 dark:bg-gray-800 dark:text-blue-gray-200">
                매매 {preview.trades}건
              </span>
              <span
                className={`rounded-full px-2.5 py-1 ${preview.configured ? "bg-blue-gray-50 text-blue-gray-700 dark:bg-gray-800 dark:text-blue-gray-200" : "bg-loss-wash text-loss"}`}
              >
                {preview.configured ? "SMTP 준비됨" : "SMTP 설정 없음"}
              </span>
            </div>
            {/* 🔴 **메일에 실리는 그대로 보여 준다** (사용자 요구 2026-08-30). 서버가 만든 HTML 이다 — 외부 입력이
                아니라 우리 조립기의 산출물이고, 같은 값을 메일 클라이언트도 렌더한다. */}
            {preview.html ? (
              <div
                className="report-html max-h-[32rem] overflow-auto rounded-lg border border-blue-gray-100 bg-white p-3 dark:border-gray-800"
                // eslint-disable-next-line react/no-danger
                dangerouslySetInnerHTML={{ __html: preview.html }}
              />
            ) : null}
            <details className="rounded-lg border border-blue-gray-100 p-3 text-sm dark:border-gray-800">
              <summary className="cursor-pointer text-blue-gray-700 dark:text-blue-gray-200">
                평문 본문 (메일 대체 본문)
              </summary>
              <pre className="mt-2 whitespace-pre-wrap font-mono text-xs text-blue-gray-800 dark:text-blue-gray-100">
                {preview.body}
              </pre>
            </details>
            {!confirming ? (
              <div>
                <Button
                  color="gray"
                  size="sm"
                  disabled={busy || !preview.configured || recipients.length === 0}
                  onClick={() => setConfirming(true)}
                >
                  보내기…
                </Button>
              </div>
            ) : (
              <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-blue-gray-900 dark:bg-amber-950/30 dark:text-white">
                <b>{recipients.join(", ")}</b> 에게 위 내용({scope})을 메일로 보낸다. 되돌릴 수 없다.
                <div className="mt-2 flex gap-2">
                  <Button color="gray" size="sm" disabled={busy} onClick={send}>
                    확인 — 보낸다
                  </Button>
                  <Button variant="text" color="blue-gray" size="sm" disabled={busy} onClick={() => setConfirming(false)}>
                    취소
                  </Button>
                </div>
              </div>
            )}
          </>
        ) : null}

        {result ? (
          <Typography variant="small" className="font-normal text-blue-gray-600 dark:text-blue-gray-300">
            {result}
          </Typography>
        ) : null}
      </CardBody>
    </Card>
  );
}
