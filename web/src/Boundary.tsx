/**
 * 렌더 오류를 **화면에 말한다** — 흰 화면 대신 (사용자 신고 2026-08-30).
 *
 * > *"가끔 상세 RUN 을 띄우면 흰 화면만 보여줘서, 새로고침을 해야만 해."*
 *
 * 🔴 React 는 렌더 중 예외가 나면 **트리 전체를 떼어낸다.** 경계가 하나도 없으면
 * 남는 것이 빈 `<div>` 뿐이고, 사람은 그것을 *"멈췄다"* 로 읽는다 — 무엇이 잘못됐는지도,
 * 어떻게 되돌리는지도 화면에 없다. 새로고침이 유일한 길이었던 이유가 그것이다.
 *
 * ⚠️ **이것은 원인을 고치는 것이 아니다.** 간헐적이라 재현을 못 했고, 그래서 다음에
 * 났을 때 **무엇이 터졌는지 남기게** 만든다 — 오류 문구를 화면에 적고 콘솔에 남긴다.
 * 원인은 그 문구를 보고 잡는다 (절대 규칙 #8: 조용한 실패를 만들지 않는다).
 *
 * ⛔ 자동으로 다시 그리지 않는다. 같은 입력이면 같은 예외라 무한 루프가 되고, 그때는
 * 화면이 깜빡이기만 한다. 다시 그리는 것은 사람이 누른다.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  children: ReactNode;
  /** 어디서 났는지 — 화면 문구에 쓴다 ("RUN 상세"). */
  where?: string;
  /** 사람이 [다시 그리기] 를 누르면 — 부모가 상태를 새로 받아오게 한다. */
  onRetry?: () => void;
};

type State = { error: Error | null };

export class Boundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // 🔴 **콘솔에도 남긴다.** 화면 문구는 한 줄이라 스택이 없다 — 원인을 잡으려면
    //    컴포넌트 스택이 필요하고, 그것은 여기서만 얻는다.
    console.error("[화면 오류]", this.props.where ?? "", error, info.componentStack);
  }

  private retry = (): void => {
    this.setState({ error: null });
    this.props.onRetry?.();
  };

  override render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    return (
      <section className="card">
        <h2>화면을 그리지 못했다{this.props.where ? ` — ${this.props.where}` : ""}</h2>
        <p className="faint">
          자료는 그대로다. <b>그리는 중에</b> 오류가 났고, 아래가 그 내용이다.
        </p>
        <pre style={{ whiteSpace: "pre-wrap" }} className="mono">
          {error.message || String(error)}
        </pre>
        <button className="btn small primary" onClick={this.retry}>
          다시 그리기
        </button>
        <p className="card-hint">
          같은 오류가 반복되면 이 문구를 그대로 남긴다 — 원인을 그것으로 잡는다.
        </p>
      </section>
    );
  }
}
