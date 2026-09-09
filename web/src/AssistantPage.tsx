/**
 * AI 투자 어시스턴트 — 큰 화면 (T257 · 사용자 2026-09-10 "챗GPT처럼"). `/assistant`.
 *
 * 챗GPT식 배치: 왼쪽 대화 목록 레일 · 가운데 넓은 본문 · 아래 입력 알약. 온보딩 위저드(T247 · 동의 → 자본 → 성향 →
 * 매매 설정 → 검토)는 여기에 **통합**된다 — 처음 온 사람에겐 빈 대화 첫 화면에 "첫 펀드 설정" 카드가 뜨고, 누르면
 * 오른쪽 패널에서 위저드가 열린다(팝업 없음). 머리의 "펀드 설정" 단추로 언제든 다시 연다.
 *
 * 작은 창의 "확대" 아이콘이 여기로 온다. 이 화면에 있으면 동그란 단추는 숨는다.
 */

import { Cog6ToothIcon, XMarkIcon } from "@heroicons/react/24/solid";
import { useEffect, useState } from "react";
import { assistantDraft, type Who } from "./api";
import { Assistant } from "./Assistant";
import { readLocal } from "./assistant";
import { ChatPanel, SLOGAN } from "./chat/ChatPanel";

export function AssistantPage({ who }: { who: Who | null }) {
  const [first, setFirst] = useState(false);
  const [wizard, setWizard] = useState(false);

  useEffect(() => {
    if (!who?.signed_in) return;
    assistantDraft()
      .then((got) => {
        const local = got.persisted ? null : readLocal();
        setFirst(got.persisted ? got.first : !(local && local.step === "done"));
      })
      .catch(() => setFirst(false));
  }, [who?.signed_in]);

  const card = first ? (
    <div className="rounded-2xl border border-blue-gray-100 bg-blue-gray-50/60 p-4 dark:border-gray-800 dark:bg-gray-800/40">
      <div className="font-semibold">첫 펀드를 아직 안 만들었다</div>
      <p className="faint mt-1 text-xs">질문 몇 개(동의 · 자본 · 성향 · 매매 설정 · 검토)로 첫 펀드를 만든다. 숫자는 전부 과거 창 실측이고 예상이 아니다.</p>
      <button type="button" className="btn primary mt-3" onClick={() => setWizard(true)}>
        첫 펀드 설정 시작
      </button>
    </div>
  ) : null;

  return (
    <div className="flex h-[calc(100vh-7.5rem)] min-h-[32rem] overflow-hidden rounded-xl border border-blue-gray-100 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center gap-3 border-b border-blue-gray-100 px-4 py-2 dark:border-gray-800">
          <div className="min-w-0">
            <b className="block text-sm">AI 투자 어시스턴트</b>
            <span className="faint block text-xs">{SLOGAN}</span>
          </div>
          <button type="button" className={`btn small ml-auto ${wizard ? "primary" : ""}`} title="첫 펀드 설정 · 기본값 편집 (온보딩 위저드)" onClick={() => setWizard((was) => !was)}>
            <Cog6ToothIcon className="h-4 w-4" /> 펀드 설정
          </button>
        </div>
        <ChatPanel who={who} wide extra={card} />
      </div>
      {wizard ? (
        <aside className="flex w-[28rem] max-w-[50%] shrink-0 flex-col border-l border-blue-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-2 border-b border-blue-gray-100 px-3 py-2 text-sm dark:border-gray-800">
            <b>펀드 설정</b>
            <button type="button" className="btn small ml-auto" onClick={() => setWizard(false)} title="닫는다">
              <XMarkIcon className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <Assistant who={who} />
          </div>
        </aside>
      ) : null}
    </div>
  );
}
