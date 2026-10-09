import { describe, expect, it } from "vitest";
import type { WhaleManagerView } from "./api";
import { initials, signedPct, slicesOf, usdCompact } from "./format";

describe("format", () => {
  it("짧은 달러", () => {
    expect(usdCompact(299_253_556_246)).toBe("$299.25B");
    expect(usdCompact(1_500_000)).toBe("$1.5M");
    expect(usdCompact(12_000)).toBe("$12K");
  });

  it("부호 있는 % · 없으면 줄표", () => {
    expect(signedPct(3.254)).toBe("+3.25%");
    expect(signedPct(-0.6)).toBe("-0.60%");
    expect(signedPct(null)).toBe("—");
  });

  it("머리글자", () => {
    expect(initials("Warren Buffett", "Berkshire")).toBe("WB");
    expect(initials("", "Tweedy, Browne")).toBe("TB");
  });

  it("원형 조각은 현물 상위 12 + 그 밖 · Put/Call 은 뺀다", () => {
    const holdings = Array.from({ length: 14 }, (_, i) => ({
      cusip: `C${i}`,
      issuer: `I${i}`,
      title: "COM",
      value_usd: 100 - i,
      shares: 1,
      sh_prn: "SH",
      put_call: null as string | null,
      weight: 0.05,
    }));
    holdings.push({ ...holdings[0]!, cusip: "PUT", put_call: "Put", weight: 0.3 });
    const view = {
      manager: { cik: "1", label: "L", person: "P", image: null, image_credit: null, image_page: null, note: null },
      reports: [{ accession: "a", form: "13F-HR", filed: "2026-08-14", period: "2026-06-30", entity: "E", total_value_usd: 1, n: 15, holdings, changes: [] }],
      disclaimer: "",
    } satisfies WhaleManagerView;
    const s = slicesOf(view, 0);
    expect(s.labels).toHaveLength(13);
    expect(s.labels[12]).toBe("그 밖 2종");
    expect(s.cusips).not.toContain("PUT");
    expect(s.series[12]).toBe(10);
  });
});
