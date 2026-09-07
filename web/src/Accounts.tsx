/**
 * 계정 · 권한 관리 — 관리자 콘솔 (사용자 요구 ② · 2026-08-30 · 기능별 권한으로 재편 2026-09-07).
 *
 * > *"[데모 거래] [실거래] [감사] [권한 관리] [리포트] [데모 계좌 조회] … 기능별 권한이 다 보이게 구성해주고,
 * >   권한 컬렉션 [게스트] [실거래 조회 게스트] [관리자] [슈퍼 관리자] 이런식으로 … 내가 정의하고 편집할 수 있게."*
 *
 * ## 두 층 — 기능(칩)과 묶음(선택 상자)
 *
 * 계정 한 줄에는 **묶음** 하나(선택 상자)와 그 묶음이 주는 **기능 칩**들이 보이고, 묶음 밖에 개별로 더 준
 * 기능은 점선 칩(× 로 뺀다). `+` 칩으로 개별 기능을 더한다. 묶음 자체는 아래 **권한 묶음** 카드에서
 * 슈퍼 관리자가 만들고 고친다 — 고치면 그 묶음을 가진 모두에게 1분 안에 듣는다.
 *
 * ## 🔴 관리자 권한은 슈퍼 관리자만 준다
 *
 * 권한 관리·묶음 편집 기능이 든 묶음/칩은 `may_roles` 가 없으면 선택 상자에서 잠긴다. 서버가 한 번 더 막는다
 * (`_apply_grant`) — 화면은 편의지 방어가 아니다.
 *
 * ## 삭제 ≠ 차단
 *
 * 삭제는 계정 **행**을 지운다 — 다시 로그인하면 새 대기 계정이 생긴다. 못 오게 하려면 차단이다. 게스트 행에는
 * 삭제가 없다(다음 게스트 입장에 다시 생긴다 · 사용자 질문 2026-09-07).
 */

import { useCallback, useEffect, useState } from "react";
import {
  accounts,
  contacts,
  deleteAccount,
  deleteRole,
  markContactHandled,
  roles,
  saveRole,
  setAccountBlocked,
  setAccountCaps,
  setAccountCollection,
  setAccountHold,
  setAccountNote,
  setHoldSettings,
  type AccountRow,
  type CapInfo,
  type ContactRow,
  type RoleCollection,
  type Who,
} from "./api";
import { LogsCard } from "./LogsCard";
import { ResourcesCard } from "./ResourcesCard";
import { ErrorCard, whenSec } from "./ui";

/** 처지 배지 — 색은 처지가 정한다. 보류·차단은 붉게, 대기는 주황, 정상은 회색. */
const ACTIVE_BADGE = { label: "정상", className: "badge" };
const STANDING: Record<string, { label: string; className: string }> = {
  active: ACTIVE_BADGE,
  pending: { label: "대기", className: "badge warn" },
  held: { label: "임시 보류", className: "badge bad" },
  blocked: { label: "차단", className: "badge bad" },
};

const ADMIN_CAPS = new Set(["manage_users", "manage_roles"]);
const LIVE_CAPS = new Set([
  "live_trade",
  "live_edit",
  "live_delete",
  "live_account_read",
  "live_runs_read",
]);
/** 배정 전에 한 번 더 묻는 기능 — 진짜 돈이 움직이거나(거래·수정·삭제) 남의 권한을 만지는 것. */
const CONFIRM_CAPS = new Set([
  "live_trade",
  "live_edit",
  "live_delete",
  "manage_users",
  "manage_roles",
]);

type Filter = "all" | "todo" | "held" | "pending" | "blocked" | "active";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "todo", label: "할 일" },
  { key: "all", label: "전체" },
  { key: "held", label: "보류" },
  { key: "pending", label: "대기" },
  { key: "active", label: "정상" },
  { key: "blocked", label: "차단" },
];

function countOf(rows: AccountRow[], key: Filter): number {
  if (key === "all") return rows.length;
  if (key === "todo")
    return rows.filter(
      (row) =>
        row.standing === "held" ||
        row.standing === "pending" ||
        row.contacts_open > 0,
    ).length;
  return rows.filter((row) => row.standing === key).length;
}

function when(text: string | null): string {
  if (!text) return "—";
  return whenSec(String(Math.floor(Date.parse(text) / 1000)));
}

/** "3시간 전" · "2일 뒤" — 표에서 시각보다 거리가 먼저 읽힌다. */
function ago(text: string | null, now: number): string {
  if (!text) return "";
  const diff = Date.parse(text) - now;
  const abs = Math.abs(diff);
  const unit =
    abs < 3_600_000
      ? `${Math.max(1, Math.round(abs / 60_000))}분`
      : abs < 86_400_000
        ? `${Math.round(abs / 3_600_000)}시간`
        : `${Math.round(abs / 86_400_000)}일`;
  return diff < 0 ? `${unit} 전` : `${unit} 뒤`;
}

function chipClass(cap: string, extra: boolean): string {
  const tone = ADMIN_CAPS.has(cap)
    ? " admin"
    : LIVE_CAPS.has(cap)
      ? " live"
      : "";
  return `chip${tone}${extra ? " extra" : ""}`;
}

/** 조치 단추 — 승인 · 보류 해제 · 차단/해제가 같은 모양이다. */
function Act({
  label,
  primary,
  danger,
  disabled,
  onClick,
}: {
  label: string;
  primary?: boolean;
  danger?: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={
        primary
          ? "btn primary small"
          : danger
            ? "btn danger small"
            : "btn small"
      }
      disabled={disabled}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

/** 기능 칩 한 줄 — 있는 것(묶음·개별)과 더하기. `labels` 는 서버가 준 이름표. */
function CapChips({
  caps,
  extra,
  labels,
  allCaps,
  canGrant,
  onAdd,
  onRemove,
}: {
  caps: string[];
  extra: string[];
  labels: Map<string, string>;
  allCaps: CapInfo[];
  canGrant: (cap: string) => boolean;
  onAdd: (cap: string) => void;
  onRemove: (cap: string) => void;
}) {
  const have = new Set(caps);
  const extras = new Set(extra);
  const addable = allCaps.filter(
    (one) => !have.has(one.key) && canGrant(one.key),
  );
  return (
    <div className="chips">
      {caps.map((cap) => (
        <span key={cap} className={chipClass(cap, extras.has(cap))} title={cap}>
          {labels.get(cap) ?? cap}
          {extras.has(cap) && canGrant(cap) ? (
            <button
              type="button"
              aria-label={`${labels.get(cap) ?? cap} 빼기`}
              onClick={() => onRemove(cap)}
            >
              ×
            </button>
          ) : null}
        </span>
      ))}
      {addable.length > 0 ? (
        <label className="chip add" title="개별 기능 더하기">
          +
          <select
            value=""
            onChange={(event) => {
              if (event.target.value) onAdd(event.target.value);
            }}
          >
            <option value="">기능 추가</option>
            {addable.map((one) => (
              <option key={one.key} value={one.key}>
                {one.group} · {one.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}

export function Accounts({ who }: { who: Who | null }) {
  const meEmail = who?.email ?? "";
  const mayRoles = Boolean(who?.may_roles);
  const [rows, setRows] = useState<AccountRow[]>([]);
  const [serverNow, setServerNow] = useState(Date.now());
  const [tableMode, setTableMode] = useState<string | undefined>(who?.mode);
  const [holdHours, setHoldHours] = useState<number | null>(null);
  const [holdDraft, setHoldDraft] = useState("");
  const [collections, setCollections] = useState<RoleCollection[]>([]);
  const [capList, setCapList] = useState<CapInfo[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("todo");
  const [query, setQuery] = useState("");
  /** 인라인 확인 패널 — `window.confirm` 대신 화면 안에서 (사용자 2026-09-07 "알림 메시지는 리스크"). */
  const [pending, setPending] = useState<{
    id: string;
    kind: "block" | "delete" | "hold" | "collection" | "cap";
    target?: string;
  } | null>(null);
  const [holdDays, setHoldDays] = useState("3");
  const [noteFor, setNoteFor] = useState<{ id: string; text: string } | null>(
    null,
  );
  const [asks, setAsks] = useState<ContactRow[]>([]);
  const [asksOpen, setAsksOpen] = useState(0);

  const labels = new Map(capList.map((one) => [one.key, one.label]));
  const byName = new Map(collections.map((one) => [one.name, one]));

  const pull = useCallback(() => {
    accounts()
      .then((body) => {
        setRows(body.rows);
        setServerNow(Date.parse(body.now) || Date.now());
        setTableMode(body.mode);
        setHoldHours(body.hold_after_hours);
        setHoldDraft((draft) => draft || String(body.hold_after_hours));
        setError("");
      })
      .catch((exc: unknown) => setError(String(exc)));
    roles()
      .then((body) => {
        setCollections(body.collections);
        setCapList(body.caps);
      })
      .catch((exc: unknown) => setError(String(exc)));
    contacts()
      .then((body) => {
        setAsks(body.rows);
        setAsksOpen(body.open);
      })
      .catch((exc: unknown) => setError(String(exc)));
  }, []);

  useEffect(() => {
    pull();
    // 보류는 시계가 만든다 — 1분마다 다시 읽어 "대기 → 보류" 가 화면에 저절로 반영되게.
    const timer = window.setInterval(pull, 60_000);
    return () => window.clearInterval(timer);
  }, [pull]);

  const act = (key: string, run: () => Promise<unknown>) => {
    setBusy(key);
    setError("");
    setPending(null);
    run()
      .then(pull)
      .catch((exc: unknown) => setError(String(exc)))
      .finally(() => setBusy(""));
  };

  /** 이 기능(들)을 내가 남에게 줄 수 있나 — 관리 기능은 슈퍼 관리자만. 서버가 같은 규칙으로 다시 막는다. */
  const canGrant = (cap: string): boolean =>
    ADMIN_CAPS.has(cap) ? mayRoles : Boolean(who?.may_admin);
  const canGrantAll = (caps: string[]): boolean => caps.every(canGrant);

  const assign = (row: AccountRow, collection: string) => {
    const target = byName.get(collection);
    const needsAsk = target
      ? target.caps.some((cap) => CONFIRM_CAPS.has(cap))
      : false;
    if (needsAsk)
      setPending({ id: row.id, kind: "collection", target: collection });
    else act(row.id, () => setAccountCollection(row.email, collection));
  };

  const addCap = (row: AccountRow, cap: string) => {
    if (CONFIRM_CAPS.has(cap))
      setPending({ id: row.id, kind: "cap", target: cap });
    else act(row.id, () => setAccountCaps(row.email, [cap], []));
  };

  const counts = {
    held: rows.filter((row) => row.standing === "held").length,
    pending: rows.filter((row) => row.standing === "pending").length,
  };

  const q = query.trim().toLowerCase();
  const shown = rows.filter((row) => {
    if (
      q &&
      !`${row.email} ${row.name} ${row.note} ${row.collection_label}`
        .toLowerCase()
        .includes(q)
    )
      return false;
    if (filter === "all") return true;
    if (filter === "todo")
      return (
        row.standing === "held" ||
        row.standing === "pending" ||
        row.contacts_open > 0
      );
    return row.standing === filter;
  });

  return (
    <>
      <div className="card">
        <h2>
          계정{" "}
          {counts.held > 0 ? (
            <b className="loss">· 임시 보류 {counts.held}</b>
          ) : null}{" "}
          {counts.pending > 0 ? (
            <b className="warn">· 승인 대기 {counts.pending}</b>
          ) : null}{" "}
          {asksOpen > 0 ? <b className="loss">· 문의 {asksOpen}</b> : null}
        </h2>
        {error ? (
          <ErrorCard
            title="계정 관리 실패"
            message={error}
            onClose={() => setError("")}
          />
        ) : null}
        <p className="muted">
          계정마다 <b>권한 묶음</b> 하나를 고르고, 묶음 밖에 필요한 기능은{" "}
          <b>+</b> 로 개별로 더한다 (점선 칩 · × 로 뺀다). 승인 없이{" "}
          <b>{holdHours ?? 24}시간</b>이 지난 대기 계정은 <b>임시 보류</b>로
          막힌다 — 묶음을 주거나 <b>유예 연장</b>으로 푼다. 실계좌·데모가 함께
          쓰는 하나의 명단이다
          {tableMode
            ? ` (지금은 ${tableMode === "live" ? "실계좌" : "데모"} 서버에서 보는 중)`
            : ""}
          .
        </p>
        <div className="row" style={{ gap: 8, alignItems: "center" }}>
          <span className="faint">보류 기준</span>
          <span className="faint">승인 없이</span>
          <input
            className="input"
            style={{ width: 84 }}
            type="number"
            min={1}
            max={720}
            step={1}
            value={holdDraft}
            onChange={(event) => setHoldDraft(event.target.value)}
          />
          <span className="faint">시간이 지나면 임시 보류</span>
          <Act
            label="저장"
            primary
            disabled={
              busy !== "" || holdDraft === "" || Number(holdDraft) === holdHours
            }
            onClick={() =>
              act("hold-after", () =>
                setHoldSettings(Number(holdDraft)).then((body) => {
                  setHoldHours(body.hold_after_hours);
                  setHoldDraft(String(body.hold_after_hours));
                }),
              )
            }
          />
        </div>
        <div className="table-tools">
          <label className="table-tools-view">
            <span className="faint">보기</span>
            <select
              className="input"
              value={filter}
              onChange={(event) => setFilter(event.target.value as Filter)}
            >
              {FILTERS.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label} ({countOf(rows, item.key)})
                </option>
              ))}
            </select>
          </label>
          <label className="search">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              className="input"
              type="search"
              placeholder="이메일 · 이름 · 메모 · 묶음 검색"
              aria-label="계정 검색"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
        </div>
        <table className="grid" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>사람</th>
              <th>처지</th>
              <th>권한 묶음 · 기능</th>
              <th>가입</th>
              <th>마지막 로그인</th>
              <th>승인</th>
              <th>메모</th>
              <th className="head-act">조치</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => {
              const self = row.email === meEmail;
              const guest = row.role === "guest";
              const badge = STANDING[row.standing] ?? ACTIVE_BADGE;
              const isPending = row.role === "pending" && !row.blocked;
              const holdsAdmin = row.caps.some((cap) => ADMIN_CAPS.has(cap));
              // 관리자 권한을 쥔 사람의 묶음은 슈퍼 관리자만 만진다 (서버 `_apply_grant` 와 같은 규칙).
              const mayTouch =
                !row.blocked &&
                (holdsAdmin ? mayRoles : Boolean(who?.may_admin));
              const grantPanel =
                pending?.id === row.id &&
                (pending.kind === "collection" || pending.kind === "cap")
                  ? pending
                  : null;
              const actPanel =
                pending?.id === row.id &&
                (pending.kind === "block" ||
                  pending.kind === "delete" ||
                  pending.kind === "hold")
                  ? pending
                  : null;
              return (
                <tr key={row.id} className={row.blocked ? "muted" : ""}>
                  <td>
                    <b>{row.name || row.email}</b>
                    <br />
                    <span className="faint">{row.email}</span>
                    {self ? <span className="faint"> · 나</span> : null}
                    {row.contacts_open > 0 ? (
                      <>
                        <br />
                        <span className="loss">
                          ✉ 문의 {row.contacts_open}건
                        </span>
                      </>
                    ) : null}
                  </td>
                  <td>
                    <span className={badge.className}>{badge.label}</span>
                    {row.standing === "pending" && row.hold_at ? (
                      <>
                        <br />
                        <span className="faint">
                          보류까지{" "}
                          {ago(
                            row.hold_released_until &&
                              Date.parse(row.hold_released_until) >
                                Date.parse(row.hold_at)
                              ? row.hold_released_until
                              : row.hold_at,
                            serverNow,
                          )}
                        </span>
                      </>
                    ) : null}
                    {row.standing === "held" && row.hold_at ? (
                      <>
                        <br />
                        <span className="faint">
                          {ago(row.hold_at, serverNow)}부터
                        </span>
                      </>
                    ) : null}
                  </td>
                  <td>
                    {grantPanel ? (
                      <ConfirmPanel
                        kind={grantPanel.kind}
                        email={row.email}
                        target={
                          grantPanel.kind === "collection"
                            ? (byName.get(grantPanel.target ?? "")?.label ??
                              grantPanel.target ??
                              "")
                            : (labels.get(grantPanel.target ?? "") ??
                              grantPanel.target ??
                              "")
                        }
                        holdDays={holdDays}
                        setHoldDays={setHoldDays}
                        busy={busy !== ""}
                        onCancel={() => setPending(null)}
                        onConfirm={() => {
                          const target = grantPanel.target ?? "";
                          if (grantPanel.kind === "collection")
                            act(row.id, () =>
                              setAccountCollection(row.email, target),
                            );
                          else
                            act(row.id, () =>
                              setAccountCaps(row.email, [target], []),
                            );
                        }}
                      />
                    ) : (
                      <div className="col" style={{ gap: 4 }}>
                        <select
                          className="input"
                          style={{ maxWidth: 220 }}
                          value={row.collection}
                          disabled={!mayTouch || busy !== "" || guest}
                          onChange={(event) => assign(row, event.target.value)}
                        >
                          {!guest ? (
                            <option value="">승인 대기 (묶음 없음)</option>
                          ) : null}
                          {collections.map((one) => (
                            <option
                              key={one.name}
                              value={one.name}
                              disabled={!canGrantAll(one.caps)}
                            >
                              {one.label}
                              {canGrantAll(one.caps) ? "" : " · 슈퍼 관리자만"}
                            </option>
                          ))}
                        </select>
                        <CapChips
                          caps={row.caps}
                          extra={row.extra_caps}
                          labels={labels}
                          allCaps={capList}
                          canGrant={(cap) => mayTouch && canGrant(cap)}
                          onAdd={(cap) => addCap(row, cap)}
                          onRemove={(cap) =>
                            act(row.id, () =>
                              setAccountCaps(row.email, [], [cap]),
                            )
                          }
                        />
                      </div>
                    )}
                  </td>
                  <td className="faint">
                    {when(row.created_at)}
                    <br />
                    <span className="faint">
                      {ago(row.created_at, serverNow)}
                    </span>
                  </td>
                  <td className="faint">{when(row.last_login_at)}</td>
                  <td className="faint">
                    {row.approved_at ? (
                      <>
                        {when(row.approved_at)}
                        <br />
                        {/* 🔴 권한을 준 사람을 안 남기면 나중에 못 따진다. */}
                        <span className="faint">{row.approved_by}</span>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="faint" style={{ maxWidth: 220 }}>
                    {noteFor?.id === row.id ? (
                      <div className="col" style={{ gap: 4 }}>
                        <textarea
                          className="input"
                          rows={2}
                          maxLength={1000}
                          value={noteFor.text}
                          onChange={(event) =>
                            setNoteFor({ id: row.id, text: event.target.value })
                          }
                        />
                        <div className="row" style={{ gap: 4 }}>
                          <Act
                            label="저장"
                            primary
                            disabled={busy !== ""}
                            onClick={() => {
                              const text = noteFor.text;
                              setNoteFor(null);
                              act(row.id, () =>
                                setAccountNote(row.email, text),
                              );
                            }}
                          />
                          <Act
                            label="취소"
                            disabled={false}
                            onClick={() => setNoteFor(null)}
                          />
                        </div>
                      </div>
                    ) : (
                      <span
                        role="button"
                        tabIndex={0}
                        title="눌러서 편집"
                        style={{ cursor: "text", whiteSpace: "pre-wrap" }}
                        onClick={() =>
                          setNoteFor({ id: row.id, text: row.note })
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter")
                            setNoteFor({ id: row.id, text: row.note });
                        }}
                      >
                        {row.note || "✎ 메모"}
                      </span>
                    )}
                  </td>
                  <td className="act">
                    {actPanel ? (
                      <ConfirmPanel
                        kind={actPanel.kind}
                        email={row.email}
                        target=""
                        holdDays={holdDays}
                        setHoldDays={setHoldDays}
                        busy={busy !== ""}
                        onCancel={() => setPending(null)}
                        onConfirm={() => {
                          if (actPanel.kind === "block")
                            act(row.id, () =>
                              setAccountBlocked(row.email, true),
                            );
                          if (actPanel.kind === "delete")
                            act(row.id, () => deleteAccount(row.email));
                          if (actPanel.kind === "hold") {
                            const days = Math.max(
                              1,
                              Math.min(365, Number(holdDays) || 1),
                            );
                            act(row.id, () => setAccountHold(row.email, days));
                          }
                        }}
                      />
                    ) : (
                      <>
                        {isPending ? (
                          <Act
                            label="승인 (열람자)"
                            primary
                            disabled={busy !== ""}
                            onClick={() => assign(row, "viewer")}
                          />
                        ) : null}
                        {isPending ? (
                          <Act
                            label={
                              row.standing === "held"
                                ? "보류 해제"
                                : "유예 연장"
                            }
                            disabled={busy !== ""}
                            onClick={() =>
                              setPending({ id: row.id, kind: "hold" })
                            }
                          />
                        ) : null}
                        {isPending &&
                        row.standing === "pending" &&
                        row.hold_released_until ? (
                          <Act
                            label="지금 보류"
                            disabled={busy !== ""}
                            onClick={() =>
                              act(row.id, () => setAccountHold(row.email, 0))
                            }
                          />
                        ) : null}
                        {!row.blocked && !self ? (
                          <Act
                            label={guest ? "게스트 입장 막기" : "차단"}
                            danger
                            disabled={busy !== ""}
                            onClick={() =>
                              setPending({ id: row.id, kind: "block" })
                            }
                          />
                        ) : null}
                        {row.blocked ? (
                          <Act
                            label={guest ? "게스트 입장 열기" : "차단 해제"}
                            primary
                            disabled={busy !== ""}
                            onClick={() =>
                              act(row.id, () =>
                                setAccountBlocked(row.email, false),
                              )
                            }
                          />
                        ) : null}
                        {/* 삭제 = 행을 지운다 — 게스트·관리자·나는 못 지운다 (게스트는 다음 입장에 다시 생긴다). */}
                        {!guest && !holdsAdmin && !self ? (
                          <Act
                            label="삭제"
                            danger
                            disabled={busy !== ""}
                            onClick={() =>
                              setPending({ id: row.id, kind: "delete" })
                            }
                          />
                        ) : null}
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
            {shown.length === 0 ? (
              <tr>
                <td colSpan={8} className="muted">
                  {rows.length === 0
                    ? "아직 아무도 로그인하지 않았다."
                    : "이 조건에 맞는 계정이 없다."}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <RolesCard
        collections={collections}
        caps={capList}
        labels={labels}
        mayRoles={mayRoles}
        busy={busy}
        onSave={(name, label, caps) =>
          act(`role:${name}`, () => saveRole(name, label, caps))
        }
        onDelete={(name) => act(`role:${name}`, () => deleteRole(name))}
      />
      <ContactsCard
        rows={asks}
        busy={busy}
        onHandled={(id) => act(id, () => markContactHandled(id))}
      />
      {/* 🔴 관리자 자리에만 놓는다 — 로그엔 주문·잔고·오류 전문이 있다 (T211). */}
      <ResourcesCard />
      <LogsCard />
    </>
  );
}

/** 되돌리기 어려운 조치의 확인 패널 — 행 안에서, 팝업 없이. */
function ConfirmPanel({
  kind,
  email,
  target,
  holdDays,
  setHoldDays,
  busy,
  onCancel,
  onConfirm,
}: {
  kind: "block" | "delete" | "hold" | "collection" | "cap";
  email: string;
  target: string;
  holdDays: string;
  setHoldDays: (value: string) => void;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const text =
    kind === "collection"
      ? `${email} 에게 [${target}] 묶음을 준다 — 실거래 주문이나 남의 권한을 만지는 기능이 들어 있다.`
      : kind === "cap"
        ? `${email} 에게 [${target}] 기능을 개별로 준다 — 진짜 돈이나 남의 권한에 닿는 기능이다.`
        : kind === "block"
          ? `${email} 을 차단한다 — 로그인이 거절된다. 삭제가 아니라 언제든 풀 수 있다.`
          : kind === "delete"
            ? `${email} 을 지운다 — 다시 로그인하면 새 대기 계정이 생긴다. 못 오게 하려면 차단이 맞다.`
            : `${email} 에게 오늘부터 그 날수만큼 유예를 준다 — 대기 상태로 둘러볼 수 있고, 그 뒤 다시 보류된다.`;
  const label =
    kind === "collection" || kind === "cap"
      ? "준다"
      : kind === "block"
        ? "차단한다"
        : kind === "delete"
          ? "지운다"
          : "푼다";
  return (
    <div className="col" style={{ gap: 6, minWidth: 220 }}>
      <span className="faint" style={{ whiteSpace: "normal" }}>
        {text}
      </span>
      {kind === "hold" ? (
        <label className="row" style={{ gap: 6, alignItems: "center" }}>
          <input
            className="input"
            style={{ width: 64 }}
            type="number"
            min={1}
            max={365}
            value={holdDays}
            onChange={(event) => setHoldDays(event.target.value)}
          />
          <span className="faint">일 동안</span>
        </label>
      ) : null}
      <div className="row" style={{ gap: 4 }}>
        <Act
          label={label}
          primary={kind === "hold" || kind === "collection" || kind === "cap"}
          danger={kind === "block" || kind === "delete"}
          disabled={busy}
          onClick={onConfirm}
        />
        <Act label="취소" disabled={busy} onClick={onCancel} />
      </div>
    </div>
  );
}

/** 권한 묶음 카드 — 슈퍼 관리자가 만들고 고친다. 관리자는 본다. */
function RolesCard({
  collections,
  caps,
  labels,
  mayRoles,
  busy,
  onSave,
  onDelete,
}: {
  collections: RoleCollection[];
  caps: CapInfo[];
  labels: Map<string, string>;
  mayRoles: boolean;
  busy: string;
  onSave: (name: string, label: string, caps: string[]) => void;
  onDelete: (name: string) => void;
}) {
  const [editing, setEditing] = useState<{
    name: string;
    label: string;
    caps: string[];
  } | null>(null);
  const [fresh, setFresh] = useState<{ name: string; label: string } | null>(
    null,
  );

  const start = (one: RoleCollection) =>
    setEditing({ name: one.name, label: one.label, caps: [...one.caps] });

  return (
    <div className="card">
      <h2>권한 묶음</h2>
      <p className="muted">
        묶음은 기능들의 이름표다 — 계정에 묶음 하나를 주면 그 안의 기능이 전부
        열린다. 묶음을 고치면 그 묶음을 가진 <b>모두</b>에게 1분 안에 듣는다.
        내장 여섯 개는 지울 수 없고 안의 기능만 고친다.
        {mayRoles ? "" : " 편집은 슈퍼 관리자만 한다."}
      </p>
      <table className="grid" style={{ width: "100%" }}>
        <thead>
          <tr>
            <th>묶음</th>
            <th>기능</th>
            <th>사용</th>
            <th className="head-act">조치</th>
          </tr>
        </thead>
        <tbody>
          {collections.map((one) => {
            const draft = editing?.name === one.name ? editing : null;
            return (
              <tr key={one.name}>
                <td>
                  {draft ? (
                    <input
                      className="input"
                      style={{ width: 160 }}
                      maxLength={40}
                      value={draft.label}
                      onChange={(event) =>
                        setEditing({ ...draft, label: event.target.value })
                      }
                    />
                  ) : (
                    <b>{one.label}</b>
                  )}
                  <br />
                  <span className="faint">
                    {one.name}
                    {one.builtin ? " · 내장" : ""}
                  </span>
                </td>
                <td>
                  {draft ? (
                    <CapChips
                      caps={draft.caps}
                      extra={draft.caps}
                      labels={labels}
                      allCaps={caps}
                      canGrant={() => true}
                      onAdd={(cap) =>
                        setEditing({ ...draft, caps: [...draft.caps, cap] })
                      }
                      onRemove={(cap) =>
                        setEditing({
                          ...draft,
                          caps: draft.caps.filter((item) => item !== cap),
                        })
                      }
                    />
                  ) : (
                    <div className="chips">
                      {one.caps.map((cap) => (
                        <span key={cap} className={chipClass(cap, false)}>
                          {labels.get(cap) ?? cap}
                        </span>
                      ))}
                      {one.caps.length === 0 ? (
                        <span className="faint">(없음)</span>
                      ) : null}
                    </div>
                  )}
                </td>
                <td className="faint">{one.in_use}명</td>
                <td className="act">
                  {mayRoles ? (
                    draft ? (
                      <>
                        <Act
                          label="저장"
                          primary
                          disabled={busy !== ""}
                          onClick={() => {
                            onSave(draft.name, draft.label, draft.caps);
                            setEditing(null);
                          }}
                        />
                        <Act
                          label="취소"
                          disabled={false}
                          onClick={() => setEditing(null)}
                        />
                      </>
                    ) : (
                      <>
                        <Act
                          label="편집"
                          disabled={busy !== ""}
                          onClick={() => start(one)}
                        />
                        {!one.builtin && one.in_use === 0 ? (
                          <Act
                            label="삭제"
                            danger
                            disabled={busy !== ""}
                            onClick={() => onDelete(one.name)}
                          />
                        ) : null}
                      </>
                    )
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {mayRoles ? (
        fresh ? (
          <div
            className="row"
            style={{ gap: 6, alignItems: "center", marginTop: 8 }}
          >
            <input
              className="input"
              style={{ width: 160 }}
              placeholder="이름 (영문 a-z 0-9 _)"
              value={fresh.name}
              onChange={(event) =>
                setFresh({ ...fresh, name: event.target.value })
              }
            />
            <input
              className="input"
              style={{ width: 200 }}
              placeholder="화면 이름"
              maxLength={40}
              value={fresh.label}
              onChange={(event) =>
                setFresh({ ...fresh, label: event.target.value })
              }
            />
            <Act
              label="만들기 (기능은 만든 뒤 편집)"
              primary
              disabled={
                busy !== "" || !/^[a-z][a-z0-9_]{1,31}$/.test(fresh.name)
              }
              onClick={() => {
                onSave(fresh.name, fresh.label || fresh.name, []);
                setFresh(null);
              }}
            />
            <Act label="취소" disabled={false} onClick={() => setFresh(null)} />
          </div>
        ) : (
          <div style={{ marginTop: 8 }}>
            <Act
              label="새 묶음"
              disabled={busy !== ""}
              onClick={() => setFresh({ name: "", label: "" })}
            />
          </div>
        )
      ) : null}
    </div>
  );
}

/** 관리자 문의 — 보류·대기 중인 사람이 남긴 말. 메일이 안 갔으면 그것도 보인다. */
function ContactsCard({
  rows,
  busy,
  onHandled,
}: {
  rows: ContactRow[];
  busy: string;
  onHandled: (id: string) => void;
}) {
  const [showDone, setShowDone] = useState(false);
  const shown = rows.filter((row) => showDone || row.handled_at === null);
  return (
    <div className="card">
      <h2>
        문의{" "}
        {rows.some((row) => row.handled_at === null) ? (
          <b className="loss">
            · 미처리 {rows.filter((row) => row.handled_at === null).length}
          </b>
        ) : null}
      </h2>
      <p className="muted">
        보류 카드의 <b>관리자 문의</b>가 남긴 기록이다. SMTP 가 설정돼 있으면
        관리자 메일로도 갔다 — 안 갔으면 여기서만 본다. 처리하면(승인·차단·답장){" "}
        <b>처리됨</b>을 누른다.
      </p>
      <label
        className="row"
        style={{ gap: 6, alignItems: "center", marginBottom: 6 }}
      >
        <input
          type="checkbox"
          checked={showDone}
          onChange={(event) => setShowDone(event.target.checked)}
        />
        <span className="faint">처리된 것도 보기</span>
      </label>
      <table className="grid">
        <thead>
          <tr>
            <th>시각</th>
            <th>사람</th>
            <th>메시지</th>
            <th>메일</th>
            <th className="head-act">조치</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr key={row.id} className={row.handled_at ? "muted" : ""}>
              <td className="faint">{when(row.created_at)}</td>
              <td>{row.email}</td>
              <td style={{ whiteSpace: "pre-wrap", maxWidth: 420 }}>
                {row.message || <span className="faint">(없음)</span>}
              </td>
              <td className="faint">{row.mailed ? "✓ 갔다" : "— 안 갔다"}</td>
              <td className="act">
                {row.handled_at ? (
                  <span className="faint">
                    {when(row.handled_at)} · {row.handled_by}
                  </span>
                ) : (
                  <Act
                    label="처리됨"
                    primary
                    disabled={busy !== ""}
                    onClick={() => onHandled(row.id)}
                  />
                )}
              </td>
            </tr>
          ))}
          {shown.length === 0 ? (
            <tr>
              <td colSpan={5} className="muted">
                {rows.length === 0
                  ? "아직 문의가 없다."
                  : "미처리 문의가 없다."}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
