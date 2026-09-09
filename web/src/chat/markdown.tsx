/**
 * 작은 마크다운 렌더러 (T257 · 챗GPT식 화면) — 모델 답의 제목 · 목록 · 표 · 굵게 · 코드 · 인용을 React 노드로.
 *
 * 라이브러리를 들이지 않는 이유: HTML 문자열을 만들지 않고 **노드를 직접 짓기** 때문에 XSS 자리가 없고, 필요한
 * 문법이 몇 개 안 된다. 모르는 문법은 글자 그대로 보인다(조용히 사라지지 않는다).
 */

import type { ReactNode } from "react";

type Block =
  | { kind: "p"; text: string }
  | { kind: "h"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "code"; text: string }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "hr" };

/** 줄들을 블록으로 자른다 (순수 · 시험 대상). */
export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  const flushParagraph = (buf: string[]) => {
    if (buf.length) out.push({ kind: "p", text: buf.join("\n") });
    buf.length = 0;
  };
  const para: string[] = [];
  while (i < lines.length) {
    const line = lines[i] ?? "";
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      flushParagraph(para);
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").trim().startsWith("```")) {
        buf.push(lines[i] ?? "");
        i += 1;
      }
      out.push({ kind: "code", text: buf.join("\n") });
      i += 1;
      continue;
    }
    if (!trimmed) {
      flushParagraph(para);
      i += 1;
      continue;
    }
    if (/^-{3,}$/.test(trimmed)) {
      flushParagraph(para);
      out.push({ kind: "hr" });
      i += 1;
      continue;
    }
    const head = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (head) {
      flushParagraph(para);
      out.push({ kind: "h", level: head[1]?.length ?? 1, text: head[2] ?? "" });
      i += 1;
      continue;
    }
    if (trimmed.startsWith("|") && (lines[i + 1] ?? "").trim().match(/^\|?\s*:?-{2,}/)) {
      flushParagraph(para);
      const cells = (row: string) =>
        row
          .trim()
          .replace(/^\|/, "")
          .replace(/\|$/, "")
          .split("|")
          .map((c) => c.trim());
      const headCells = cells(trimmed);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && (lines[i] ?? "").trim().startsWith("|")) {
        rows.push(cells(lines[i] ?? ""));
        i += 1;
      }
      out.push({ kind: "table", head: headCells, rows });
      continue;
    }
    if (/^[-*•]\s+/.test(trimmed)) {
      flushParagraph(para);
      const items: string[] = [];
      while (i < lines.length && /^[-*•]\s+/.test((lines[i] ?? "").trim())) {
        items.push((lines[i] ?? "").trim().replace(/^[-*•]\s+/, ""));
        i += 1;
      }
      out.push({ kind: "ul", items });
      continue;
    }
    if (/^\d+[.)]\s+/.test(trimmed)) {
      flushParagraph(para);
      const items: string[] = [];
      while (i < lines.length && /^\d+[.)]\s+/.test((lines[i] ?? "").trim())) {
        items.push((lines[i] ?? "").trim().replace(/^\d+[.)]\s+/, ""));
        i += 1;
      }
      out.push({ kind: "ol", items });
      continue;
    }
    if (trimmed.startsWith(">")) {
      flushParagraph(para);
      const buf: string[] = [];
      while (i < lines.length && (lines[i] ?? "").trim().startsWith(">")) {
        buf.push((lines[i] ?? "").trim().replace(/^>\s?/, ""));
        i += 1;
      }
      out.push({ kind: "quote", text: buf.join("\n") });
      continue;
    }
    para.push(trimmed);
    i += 1;
  }
  flushParagraph(para);
  return out;
}

/** 한 줄 안의 **굵게** · *기울임* · `코드` 를 노드로. */
export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<b key={key++}>{tok.slice(2, -2)}</b>);
    else if (tok.startsWith("`")) out.push(<code key={key++} className="mono rounded bg-blue-gray-50 px-1 dark:bg-gray-800">{tok.slice(1, -1)}</code>);
    else out.push(<i key={key++}>{tok.slice(1, -1)}</i>);
    last = at + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text);
  return (
    <div className="md space-y-2">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "h": {
            const cls = b.level <= 2 ? "text-base font-semibold" : "text-sm font-semibold";
            return (
              <div key={i} className={`${cls} mt-2`}>
                {inline(b.text)}
              </div>
            );
          }
          case "ul":
            return (
              <ul key={i} className="list-disc space-y-0.5 pl-5">
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={i} className="list-decimal space-y-0.5 pl-5">
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it)}</li>
                ))}
              </ol>
            );
          case "quote":
            return (
              <blockquote key={i} className="border-l-2 border-blue-gray-200 pl-3 text-blue-gray-600 dark:border-gray-700 dark:text-blue-gray-300">
                {inline(b.text)}
              </blockquote>
            );
          case "code":
            return (
              <pre key={i} className="mono overflow-x-auto rounded-lg bg-blue-gray-50 p-2 text-xs dark:bg-gray-800">
                {b.text}
              </pre>
            );
          case "table":
            return (
              <div key={i} className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      {b.head.map((h, j) => (
                        <th key={j}>{inline(h)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j}>
                        {r.map((c, k) => (
                          <td key={k}>{inline(c)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "hr":
            return <hr key={i} className="border-blue-gray-100 dark:border-gray-800" />;
          default:
            return (
              <p key={i} className="whitespace-pre-wrap leading-relaxed">
                {inline(b.text)}
              </p>
            );
        }
      })}
    </div>
  );
}
