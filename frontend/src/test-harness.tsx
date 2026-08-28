// 화면 테스트가 함께 쓰는 것. **`fetch`를 경로별 표로 가짜로 만든다** — MSW 같은 층을
// 넣지 않는다. 요점은 "이 경로를 이 query로 불렀나"와 "이 응답을 어떻게 그리나" 둘뿐이다.

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

export interface FetchLog {
  paths: string[];
}

/**
 * 경로 접두 → 응답 표. 값이 숫자면 그 상태 코드로 실패시킨다.
 *
 * 불린 경로를 전부 기록해 두어서, URL query가 실제 요청까지 갔는지 테스트가 확인할 수 있다.
 */
export function stubFetch(table: Record<string, unknown>): FetchLog {
  const log: FetchLog = { paths: [] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      log.paths.push(path);
      const key = Object.keys(table)
        .sort((a, b) => b.length - a.length)
        .find((prefix) => path.startsWith(prefix));
      const value = key === undefined ? 404 : table[key];
      if (typeof value === "number") {
        return new Response("{}", { status: value, statusText: "Not Found" });
      }
      return new Response(JSON.stringify(value), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return log;
}

/** 라우트 하나를 그 경로에 세운다. 페이지가 `useParams`·`useSearchParams`를 쓰기 때문이다. */
export function renderAt(path: string, route: string, element: ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={route} element={element} />
      </Routes>
    </MemoryRouter>,
  );
}
