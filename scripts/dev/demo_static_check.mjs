// 정적 데모 화면 점검 — 게스트로 들어가 화면마다 실패한 /api 요청 수를 세고 한 장씩 찍는다.
//   BASE=http://127.0.0.1:5175 OUT=<폴더> node demo_static_check.mjs   (X-Forwarded-Proto: https 로 운영 정적 데모를 흉내)
import { chromium } from "playwright";

const base = process.env.BASE ?? "http://127.0.0.1:5175";
const out = process.env.OUT ?? "/tmp";
const ROUTES = (process.env.ROUTES ?? "/console,/paper,/report,/calendar,/evidence,/chart-order").split(",");
const b = await chromium.launch();
const ctx = await b.newContext({
  viewport: { width: 1280, height: 900 },
  locale: "ko-KR",
  timezoneId: "Asia/Seoul",
  extraHTTPHeaders: process.env.NO_TLS ? {} : { "X-Forwarded-Proto": "https" },
});
const p = await ctx.newPage();
const fails = [];
p.on("response", (r) => {
  const u = new URL(r.url());
  if (u.pathname.startsWith("/api/") && r.status() >= 400) fails.push(`${r.status()} ${r.request().method()} ${u.pathname}`);
});
await p.goto(base + "/", { waitUntil: "networkidle" }).catch(() => {});
await p.screenshot({ path: `${out}/00_first.png` });
const guest = p.getByText("게스트로 둘러보기");
console.log("게스트 단추", await guest.count());
if (await guest.count()) {
  await guest.first().click();
  await p.waitForURL(/console/, { timeout: 20000 }).catch(() => {});
}
let k = 1;
const shot = async (route) => {
  const before = fails.length;
  await p.goto(base + route, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await p.waitForTimeout(3000);
  const name = `${String(k++).padStart(2, "0")}_${route.replace(/\W+/g, "_")}`;
  await p.screenshot({ path: `${out}/${name}.png`, fullPage: false });
  console.log(`${route} · 실패 ${fails.length - before}`, fails.slice(before).slice(0, 6).join(" | "));
};
for (const r of ROUTES) await shot(r);
const run = await p.locator('a[href^="/paper/"]').first().getAttribute("href").catch(() => null);
if (run) await shot(run);
await b.close();
