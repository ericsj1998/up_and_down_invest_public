/**
 * 앱 — 라우터 + 셸 (T220 · 2026-09-05 재편).
 *
 * 🔴 **콘솔이 홈이다** (사용자 확정 2026-08-19). 거래소가 말하는 **사실**이 첫 화면이고, RUN 은 거기서 골라
 * 들어간다 — RUN 화면을 홈으로 두면 RUN 이 없을 때 빈 화면이 뜨고, 여럿일 때 화면이 임의로 하나를 고른다.
 *
 * ⚠️ **주소는 옛 화면과 같다** — `/console` `/report` `/accounts` `/paper/:run` `/label`. 저장해 둔 링크가 살아야 한다.
 *
 * 🔴 **화면을 변수에 담아 렌더하지 않는다** (2026-08-19 실측). 라우트의 `element` 는 고정 JSX 다 — 컴포넌트
 *    신원이 렌더마다 바뀌면 React 가 그 아래를 통째로 언마운트-리마운트해 차트가 처음부터 다시 그려진다.
 */
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { Accounts } from "./Accounts";
import { AiReport } from "./AiReport";
import { assistantDraft, type Who } from "./api";
import { Assistant } from "./Assistant";
import { readLocal } from "./assistant";
import { Boundary } from "./Boundary";
import { ConsoleTab } from "./ConsoleTab";
import { AuthTroubleNote, Gate, PendingNote, useMe } from "./Gate";
import { PaperTab } from "./PaperTab";
// ⭐ 리포트 대시보드는 **늦게 받는다** — apexcharts(~150 kB gz)가 그 화면에만 쓰인다. 콘솔이 홈이라 첫 로딩이
//    거기에 갇힐 이유가 없다 (T220 6단계 · 번들 1.78 MB 중 차트 몫을 뗀다).
const ReportDashboard = lazy(() =>
  import("./ReportDashboard").then((m) => ({ default: m.ReportDashboard })),
);
// 근거 화면(T222)도 차트를 쓴다 — 같은 이유로 늦게 받는다.
const Evidence = lazy(() =>
  import("./Evidence").then((m) => ({ default: m.Evidence })),
);
import { AuthFrame, Layout } from "./shell/Layout";
import { LABELS_ON, pageTitle } from "./shell/nav";
import { OpenRunsProvider, useOpenRuns } from "./shell/openRuns";
import { ToTop } from "./ui";

// 🔬 라벨(임시) 화면은 배포에서 뺀다 (T214). `VITE_LABELS` 가 0 이면 아래 `import()` 가 죽은 가지가 되어
//    청크 자체가 dist 에 안 나온다. 코드는 지우지 않는다.
const LabelTab = LABELS_ON
  ? lazy(() => import("./LabelTab").then((m) => ({ default: m.LabelTab })))
  : null;

export function App() {
  return (
    <BrowserRouter>
      <OpenRunsProvider>
        <Shell />
      </OpenRunsProvider>
    </BrowserRouter>
  );
}

/** 첫 화면 — 서버가 "처음" 이라 하면 어시스턴트, 아니면 콘솔. 게스트는 브라우저 초안이 끝났으면 콘솔. */
function Home({ who }: { who: Who | null }) {
  const [to, setTo] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    if (!who?.signed_in) {
      setTo("/console");
      return;
    }
    assistantDraft()
      .then((got) => {
        if (!alive) return;
        const local = got.persisted ? null : readLocal();
        const first = got.persisted ? got.first : !(local && local.step === "done");
        setTo(first ? "/assistant" : "/console");
      })
      .catch(() => alive && setTo("/console"));
    return () => {
      alive = false;
    };
  }, [who?.signed_in]);
  if (!to) return <p className="faint">첫 화면을 정하는 중…</p>;
  return <Navigate to={to} replace />;
}

function Shell() {
  // 🔴 **문은 화면 전체를 감싼다** (2026-08-30). 다만 이것은 편의지 방어가 아니다 — 진짜 방어는 API
  //    미들웨어에 있다. URL 을 아는 사람은 화면을 안 거친다.
  const { who, loading, refresh } = useMe();
  const { pathname } = useLocation();
  const where = pathname.startsWith("/paper/")
    ? "RUN 상세"
    : (pageTitle(pathname) ?? pathname);

  const body = (
    <>
      {/* 🔴 인증 때문에 막힌 것은 **여기 한 곳**에서 받는다 (요구 ③). 문 밖에 두는 이유는 로그인이 풀렸을 때도
          보여야 해서다. */}
      <AuthTroubleNote onSignedOut={refresh} />
      <Gate who={who} loading={loading}>
        {/* ⚠️ 읽기는 되지만 주문은 안 되는 사람에게 **미리** 말한다 (사용자 확정: 미승인자 읽기 전용). */}
        {who?.signed_in ? <PendingNote who={who} /> : null}
        {/* 🔴 **흰 화면을 만들지 않는다** (사용자 신고 2026-08-30). 렌더 중 예외가 나면 React 는 트리 전체를
            떼어낸다 — 경계가 없으면 남는 것이 빈 화면뿐이다. 원인을 고치는 것이 아니라 **다음에 났을 때
            말하게** 하는 것이다 (규칙 #8). */}
        <Boundary where={where} onRetry={refresh}>
          <Routes>
            {/* ⭐ T247 — 초안도 펀드도 없는 사람은 어시스턴트가 첫 화면을 잡는다. 판정은 서버(`/assistant/draft.first`). */}
            <Route path="/" element={<Home who={who} />} />
            <Route path="/assistant" element={<Assistant who={who} />} />
            <Route path="/console" element={<ConsolePage />} />
            <Route
              path="/report"
              element={
                <Suspense
                  fallback={<p className="faint">리포트 화면을 불러오는 중…</p>}
                >
                  <ReportDashboard />
                </Suspense>
              }
            />
            <Route
              path="/evidence"
              element={
                <Suspense
                  fallback={<p className="faint">근거 화면을 불러오는 중…</p>}
                >
                  <Evidence />
                </Suspense>
              }
            />
            <Route path="/paper/:run" element={<PaperPage />} />
            <Route path="/paper" element={<Navigate to="/console" replace />} />
            <Route path="/accounts" element={<Accounts who={who} />} />
            <Route path="/ai-report" element={<AiReport who={who} />} />
            <Route path="/label" element={<LabelPage />} />
            <Route path="*" element={<Missing />} />
          </Routes>
        </Boundary>
      </Gate>
      {/* ⭐ 화면이 길다 — 순위·매매 로그·안전장치까지 내려가면 위가 멀다. */}
      <ToTop />
    </>
  );

  // 로그인 전에는 사이드바를 보여 줄 이유가 없다 — 문(Gate)만 가운데 카드로.
  return who?.signed_in ? (
    <Layout who={who} onOut={refresh}>
      {body}
    </Layout>
  ) : (
    <AuthFrame>{body}</AuthFrame>
  );
}

function ConsolePage() {
  const runs = useOpenRuns();
  const navigate = useNavigate();
  const openRun = useCallback(
    (run: string, name?: string) => {
      runs.open(run, name);
      navigate(`/paper/${run}`);
    },
    [runs, navigate],
  );
  return <ConsoleTab openRun={openRun} />;
}

function PaperPage() {
  const { run = "" } = useParams();
  const runs = useOpenRuns();
  const navigate = useNavigate();

  // ⭐ **주소로 바로 들어와도 목록에 생긴다.** 저장해 둔 링크·새 창이 그 경로다.
  useEffect(() => {
    if (run) runs.open(run);
  }, [run, runs]);

  const home = useCallback(() => navigate("/console"), [navigate]);

  if (!run) return <Navigate to="/console" replace />;
  return <PaperTab run={run} home={home} named={runs.remember} />;
}

function LabelPage() {
  if (!LabelTab) {
    // 🔴 주소창에 `/label` 을 쳐도 빈 화면을 주지 않는다 (규칙 #8).
    return (
      <section className="card">
        <h2>라벨(임시)</h2>
        <p className="faint">
          이 배포에는 라벨 도구가 들어 있지 않습니다. 연구용 dev 빌드에서만
          켜집니다.
        </p>
      </section>
    );
  }
  return (
    <Suspense fallback={<p className="faint">라벨 화면을 불러오는 중…</p>}>
      <LabelTab />
    </Suspense>
  );
}

function Missing() {
  // 🔴 없는 주소(옛 `/live` · `/order` 포함)에도 빈 화면을 주지 않는다 (규칙 #8).
  return (
    <section className="card">
      <h2>없는 화면</h2>
      <p className="faint">
        이 주소에는 화면이 없습니다. 왼쪽에서 <b>거래 콘솔</b>이나 <b>리포트</b>
        로 가세요.
      </p>
    </section>
  );
}
