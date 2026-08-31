// 첫 화면. **"지금 무엇이 돌고 있나"를 한 눈에** 답한다.
//
// 주제 셋: ① 이미 있는 조회를 나란히 부른다(대시보드 전용 라우트를 만들지 않는다)
// ② 없는 것을 0으로 채우지 않는다 ③ 카드마다 그 화면으로 가는 링크가 있다.

import { screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { renderAt, stubFetch } from "../test-harness";
import DashboardPage from "./DashboardPage";

afterEach(() => vi.unstubAllGlobals());

const HEALTH = {
  items: [
    {
      source: "mof",
      source_type: "api",
      records: 2,
      succeeded: 2,
      failed: 0,
      running: 0,
      quarantined: 0,
      rows: 20,
      latest_at: "2026-08-30T23:20:00Z",
      latest_status: null,
    },
    {
      source: "kis",
      source_type: "api",
      records: 10,
      succeeded: 9,
      failed: 1,
      running: 0,
      quarantined: 0,
      rows: 100,
      latest_at: "2026-08-31T05:00:00Z",
      latest_status: null,
    },
  ],
  since: "2026-08-30T15:00:00Z",
  limit: 5,
  offset: 0,
  has_more: false,
};

const EMPTY = { items: [], limit: 5, offset: 0, has_more: false };

/** 나머지 카드는 비워 두고 한 카드만 본다. 대시보드는 조회 여섯을 나란히 부른다. */
const ALL_EMPTY = {
  "/api/collection/health": EMPTY,
  "/api/llm-runs": EMPTY,
  "/api/theses": EMPTY,
  "/api/causal/paths": EMPTY,
  "/api/causal/directions": EMPTY,
  "/api/documents": EMPTY,
};

it("이미 있는 조회를 나란히 부른다", async () => {
  // **대시보드 전용 집계 라우트를 만들지 않는다.** 그 라우트만 아는 규칙이 생기면 화면이
  // 바뀔 때마다 서버를 고쳐야 한다.
  const log = stubFetch({
    "/api/collection/health": HEALTH,
    "/api/llm-runs": EMPTY,
    "/api/theses": EMPTY,
    "/api/causal/paths": EMPTY,
    "/api/documents": EMPTY,
    "/api/causal/directions": EMPTY,
  });
  renderAt("/dashboard", "/dashboard", <DashboardPage />);

  await screen.findByRole("heading", { name: "대시보드" });
  for (const prefix of [
    "/api/collection/health",
    "/api/llm-runs",
    "/api/theses",
    "/api/causal/paths",
    "/api/causal/directions",
    "/api/documents",
  ]) {
    expect(log.paths.some((path) => path.startsWith(prefix))).toBe(true);
  }
});

it("가장 오래 소식 없는 출처가 먼저다", async () => {
  // 이 화면의 질문이 "무엇이 안 들어오고 있나"라서 요약의 첫 줄이 곧 답이다.
  stubFetch({ ...ALL_EMPTY, "/api/collection/health": HEALTH });
  renderAt("/dashboard", "/dashboard", <DashboardPage />);

  expect(await screen.findByText(/mof/)).toBeTruthy();
  // 실패가 있는 출처는 따로 말한다.
  expect(screen.getByText(/kis/)).toBeTruthy();
});

it("없는 것을 0으로 채우지 않는다", async () => {
  stubFetch(ALL_EMPTY);
  renderAt("/dashboard", "/dashboard", <DashboardPage />);

  await screen.findByRole("heading", { name: "대시보드" });
  expect(screen.getByText(/최근 24시간에 수집 기록이 없다/)).toBeTruthy();
  expect(screen.getByText(/실행 기록이 없다/)).toBeTruthy();
  expect(screen.getByText(/최근 3주에 경로가 없다/)).toBeTruthy();
});

it("카드 제목이 그 화면으로 가는 링크다", async () => {
  stubFetch(ALL_EMPTY);
  renderAt("/dashboard", "/dashboard", <DashboardPage />);

  await screen.findByRole("heading", { name: "대시보드" });
  expect(screen.getByRole("link", { name: "수집" }).getAttribute("href")).toBe("/collection");
  expect(screen.getByRole("link", { name: "인과 그래프" }).getAttribute("href")).toBe("/causal");
});
