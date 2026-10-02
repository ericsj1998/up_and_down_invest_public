// 정적 데모 저장본 만들기 (2026-10-02 · 포트폴리오 데모) — 지금 도는 데모를 게스트로 둘러보며 화면이 부르는
// `/api` GET 응답(200 · JSON)을 `OUT/__demo/<경로>.json` 과 `OUT/__demo/<경로>__<질의>.json` 에 담는다.
// nginx(web/nginx.conf)가 데모 행선지 요청을 이 파일로 답한다 — 데모 API 프로세스 없이.
//
//   bash scripts/dev/demo_snapshot.sh        (playwright 는 ~/tools/pw · 주소는 scripts/ops/host.env · 찍지 않는다)
//
// 같은 경로가 여러 번 오면 **처음 것**을 지킨다(첫 화면 상태). 질의가 붙은 응답은 질의별로도 남긴다.
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base = process.env.BASE;
const out = process.env.OUT;
if (!base || !out) {
  console.error("BASE · OUT 이 필요하다");
  process.exit(2);
}
const ROUTES = [
  "/console",
  "/paper",
  "/report",
  "/ai-report",
  "/assistant",
  "/calendar",
  "/chart-order",
  "/evidence",
  "/grading",
  "/accounts",
  "/tokens",
];
const RUNS = Number(process.env.RUNS ?? 8);

const saved = new Set();
let bytes = 0;
const write = (rel, body) => {
  const file = path.join(out, "__demo", rel + ".json");
  if (saved.has(file) || fs.existsSync(file)) return;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, body);
  saved.add(file);
  bytes += body.length;
};

const b = await chromium.launch();
const ctx = await b.newContext({
  viewport: { width: 1280, height: 900 },
  locale: "ko-KR",
  timezoneId: "Asia/Seoul",
});
const p = await ctx.newPage();
p.on("response", async (r) => {
  const req = r.request();
  if (req.method() !== "GET" || r.status() !== 200) return;
  const u = new URL(r.url());
  if (!u.pathname.startsWith("/api/")) return;
  if (!(r.headers()["content-type"] ?? "").includes("json")) return;
  let body;
  try {
    body = await r.body();
  } catch {
    return;
  }
  const rel = u.pathname.slice("/api".length).replace(/\/+$/, "") || "/index";
  if (rel === "/auth/me") {
    // 입장 전(signed_in false)과 게스트 입장 뒤를 따로 — nginx 가 게스트 쿠키로 둘 중 하나를 낸다.
    let me = {};
    try {
      me = JSON.parse(body.toString());
    } catch {
      return;
    }
    write(me.signed_in ? "/auth/me" : "/auth/me_anon", body);
    return;
  }
  write(rel, body);
  if (u.search.length > 1) write(`${rel}__${u.search.slice(1)}`, body);
});

await p.goto(base + "/", { waitUntil: "networkidle" }).catch(() => {});
const guest = p.getByText("게스트로 둘러보기");
if (await guest.count()) {
  await guest.first().click();
  await p.waitForURL(/console/, { timeout: 30000 }).catch(() => {});
}
const visit = async (route) => {
  await p.goto(base + route, { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
  await p.waitForTimeout(Number(process.env.WAIT ?? 5000));
  await p.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => {});
  await p.waitForTimeout(1500);
  console.log(`화면 ${route} · 지금까지 ${saved.size}개`);
};
for (const route of ROUTES) await visit(route);
// 판 상세 — 콘솔 · RUN 목록에 걸린 판 주소 몇 개
const runs = new Set();
for (const route of ["/console", "/paper"]) {
  await p.goto(base + route, { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
  await p.waitForTimeout(3000);
  for (const href of await p.locator('a[href^="/paper/"]').evaluateAll((els) => els.map((e) => e.getAttribute("href")))) {
    if (href) runs.add(href);
  }
}
for (const run of [...runs].slice(0, RUNS)) await visit(run);
await b.close();
console.log(`저장 ${saved.size}개 · ${(bytes / 1024).toFixed(0)} KB · 판 상세 ${Math.min(runs.size, RUNS)}/${runs.size}`);
