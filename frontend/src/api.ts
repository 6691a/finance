// `/api` 하나를 상대 경로로 친다. **base URL을 두지 않는다** — 운영은 FastAPI가 같은
// origin에서 화면과 API를 함께 주고, 개발은 Vite가 `/api`만 프록시한다.
//
// non-2xx는 `ApiError` 하나로 바꾼다. 화면은 상태 코드로만 갈라 404와 그 밖을 구분한다.

import { useEffect, useState } from "react";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** 값이 있는 것만 붙인 query string. 배열은 같은 이름을 여러 번 준다. */
export function query(params: Record<string, string | number | string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    if (Array.isArray(value)) value.forEach((entry) => search.append(name, entry));
    else search.append(name, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const reply = await fetch(path, { signal: signal ?? null, headers: { Accept: "application/json" } });
  if (!reply.ok) throw new ApiError(reply.status, `${reply.status} ${reply.statusText}`);
  return (await reply.json()) as T;
}

export interface Resource<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** 오류 화면의 재시도. 이전 성공 데이터를 최신처럼 남기지 않는다. */
  reload: () => void;
}

/**
 * 한 경로를 읽어 상태 셋으로 준다. **리소스별 hook 계층이 아니다** — 페이지가 자기
 * 경로를 만들고 이것은 `fetch` + `AbortController` + 상태 셋의 반복만 없앤다.
 *
 * 경로가 바뀌면 이전 요청을 취소한다. 취소를 안 하면 느린 응답이 나중에 도착해 이미
 * 떠난 화면의 상태를 덮어쓴다.
 *
 * **`null`을 주면 아예 부르지 않는다.** 화면이 아직 필수 인자를 못 받은 상태(종목인데
 * 거래소를 안 골랐다)에서 서버로 422를 받으러 가지 않으려는 것이다. hook은 조건부로
 * 부를 수 없으므로 그 판단을 인자로 받는다.
 */
export function useJson<T>(path: string | null): Resource<T> {
  // **답을 그 답이 온 경로와 함께 들고 있는다.** 경로만 바꾸고 데이터를 그대로 두면,
  // 새 경로의 응답이 오기 전 한 렌더 동안 **새 화면이 옛 응답을 읽는다** — 공매도
  // 렌더러가 대차거래 응답을 받아 `rows.length`에서 죽는 식이다(2026-08-27 실측).
  // 지우는 일을 effect에 맡기면 그 한 렌더를 못 막는다. effect는 그린 뒤에 돌기 때문이다.
  const [answer, setAnswer] = useState<{ path: string; value: T | null; error: unknown } | null>(
    null,
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    getJson<T>(path, controller.signal)
      .then((value) => setAnswer({ path, value, error: null }))
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setAnswer({ path, value: null, error: reason });
      });
    return () => controller.abort();
  }, [path, attempt]);

  // 이 경로의 답인가. 아니면 아직 없는 것과 같다.
  const fresh = answer !== null && answer.path === path;

  return {
    data: fresh ? answer.value : null,
    error: fresh ? answer.error : null,
    loading: path !== null && !fresh,
    // **재시도는 옛 답을 먼저 버린다.** 이전 성공·실패를 최신처럼 남기지 않는다.
    reload: () => {
      setAnswer(null);
      setAttempt((value) => value + 1);
    },
  };
}
