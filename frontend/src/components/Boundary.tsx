// 렌더 중에 터진 예외를 화면에 남긴다. **흰 화면을 만들지 않는다.**
//
// React는 렌더 예외를 만나면 그 트리를 통째로 언마운트한다 — 아무 것도 없는 페이지가
// 남고, 콘솔을 열지 않은 사람에게는 "안 그려진다"로만 보인다. 무엇이 터졌는지 화면이
// 말해야 그 다음이 있다.
//
// **에러 경계는 클래스여야 한다.** hook에는 대응하는 것이 없다(React 19 기준).

import { Component, type ReactNode } from "react";

interface State {
  message: string | null;
}

export default class Boundary extends Component<{ children: ReactNode }, State> {
  override state: State = { message: null };

  static getDerivedStateFromError(error: unknown): State {
    return { message: error instanceof Error ? error.message : String(error) };
  }

  override render(): ReactNode {
    if (this.state.message === null) return this.props.children;
    return (
      <div className="state" role="alert">
        <p>이 화면을 그리다 멈췄다.</p>
        {/* 응답 모양이 바뀌었을 때 어느 칸인지가 이 한 줄에 있다. */}
        <p>
          <code>{this.state.message}</code>
        </p>
        <button type="button" onClick={() => this.setState({ message: null })}>
          다시 그리기
        </button>
      </div>
    );
  }
}
