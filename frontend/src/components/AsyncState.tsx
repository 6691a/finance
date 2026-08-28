// 로딩·빈 결과·404·일반 오류. **페이지마다 제각각 만들지 않는다.**
//
// 넷을 한 파일에 두는 이유는 이것들이 같은 결정의 네 갈래이기 때문이다 — 어느 화면이든
// "아직 없다 / 없다 / 못 찾았다 / 실패했다" 중 하나를 같은 말로 보여야 한다.

import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { ApiError, type Resource } from "../api";

export function Loading() {
  return (
    <p className="state" role="status">
      불러오는 중…
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="state">{children}</p>;
}

export function NotFound({ what, back }: { what: string; back: string }) {
  return (
    <div className="state">
      <p>{what}이(가) 없다.</p>
      <Link to={back}>목록으로</Link>
    </div>
  );
}

/**
 * 일반 오류. **이전 성공 데이터를 거짓으로 최신처럼 남기지 않는다** — 화면을 이것으로
 * 갈아 끼우고 재시도 버튼만 준다. 내부 SQL·stack trace는 응답에 없고 화면도 싣지 않는다.
 */
export function Failed({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="state" role="alert">
      <p>불러오지 못했다.</p>
      <button type="button" onClick={onRetry}>
        다시 시도
      </button>
    </div>
  );
}

/**
 * 상태 넷을 한 자리에서 가른다. 데이터가 왔을 때만 `children`을 부른다.
 *
 * 404를 따로 가르는 이유는 "없는 것"과 "실패한 것"의 다음 행동이 다르기 때문이다 —
 * 앞은 목록으로 돌아가는 것이고 뒤는 재시도다.
 */
export function Async<T>({
  resource,
  what,
  back,
  children,
}: {
  resource: Resource<T>;
  what: string;
  back: string;
  children: (data: T) => ReactNode;
}) {
  if (resource.error instanceof ApiError && resource.error.status === 404) {
    return <NotFound what={what} back={back} />;
  }
  if (resource.error) return <Failed onRetry={resource.reload} />;
  if (resource.loading || resource.data === null) return <Loading />;
  return <>{children(resource.data)}</>;
}
