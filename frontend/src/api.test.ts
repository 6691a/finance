import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, getJson, query } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("query", () => {
  it("값이 없는 칸은 붙이지 않는다", () => {
    expect(query({ from: "", to: undefined, slot: "pre_open" })).toBe("?slot=pre_open");
  });

  it("배열은 같은 이름을 여러 번 준다", () => {
    // FastAPI의 `list[str]` 파라미터가 이 모양을 받는다.
    expect(query({ horizon_days: ["0", "5"] })).toBe("?horizon_days=0&horizon_days=5");
  });

  it("빈 필터는 물음표조차 만들지 않는다", () => {
    expect(query({})).toBe("");
  });
});

describe("getJson", () => {
  it("non-2xx를 ApiError 하나로 바꾼다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404, statusText: "Not Found" })));

    await expect(getJson("/api/theses/999")).rejects.toBeInstanceOf(ApiError);
    await expect(getJson("/api/theses/999")).rejects.toMatchObject({ status: 404 });
  });

  it("500도 같은 타입이고 상태 코드로만 갈린다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500, statusText: "Server Error" })));

    await expect(getJson("/api/theses")).rejects.toMatchObject({ status: 500 });
  });

  it("base URL 없이 상대 경로를 그대로 친다", async () => {
    const spy = vi.fn(async (path: string) => {
      void path;
      return new Response("[]", { status: 200 });
    });
    vi.stubGlobal("fetch", spy);

    await getJson("/api/theses");

    expect(spy.mock.calls[0]?.[0]).toBe("/api/theses");
  });
});
