// 클라이언트 라우트 집합과 API 오류 상태.
//
// **라우트 집합을 리터럴로 대조한다.** backend의 `test_routes.py`와 같은 성격이다 —
// 라우트를 등록하지 않은 실수를 잡는다.

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import App from "./App";
import {
  ACCURACY,
  CALL_DETAIL,
  FORECAST_DETAIL,
  FORECAST_LIST,
  MEMORY_LIST,
  OBSERVATION_LIST,
  QUALITY,
  RELATION_GRAPH,
  RELATION_LIST,
  RUN_DETAIL,
  RUN_LIST,
} from "./fixtures";
import { stubFetch } from "./test-harness";

// jsdom에는 canvas도 `matchMedia`도 없다. 여기서 볼 것은 라우트 등록이지 그리기가 아니라
// 둘 다 가짜로 둔다 — lifecycle은 `GraphView.test.tsx`와 `ChartView.test.tsx`가 본다.
vi.mock("uplot/dist/uPlot.min.css", () => ({}));
vi.mock("uplot", () => {
  class FakePlot {
    setData = vi.fn();
    setSize = vi.fn();
    destroy = vi.fn();
    static paths = { bars: () => () => null };
  }
  return { default: FakePlot };
});

vi.mock("cytoscape", () => ({
  default: () => ({
    on: vi.fn(),
    add: vi.fn(),
    destroy: vi.fn(),
    fit: vi.fn(),
    reset: vi.fn(),
    layout: () => ({ run: vi.fn() }),
    elements: () => ({ remove: vi.fn(), removeClass: vi.fn() }),
    getElementById: () => ({ addClass: vi.fn() }),
  }),
}));

afterEach(() => vi.unstubAllGlobals());

const CLIENT_ROUTES = [
  "/",
  "/documents",
  "/documents/:documentId",
  "/positioning",
  "/events",
  "/collection",
  "/quotes",
  "/quotes/:kind/:symbol",
  "/indicators",
  "/indicators/curve",
  "/indicators/:provider/:seriesId",
  "/forecast",
  "/forecast/:runDate/:slot",
  "/relations",
  "/relations/:factor",
  "/memories",
  "/runs",
  "/runs/:llmRunId",
  "/runs/:llmRunId/tool-calls/:seq",
  "/quality",
];

function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

it("화면 루트는 대시보드다", async () => {
  // 실행 목록은 "무슨 일이 있었나"의 한 갈래일 뿐이라 수집·전망·관계가 안 보인다.
  stubFetch({
    "/api/llm-runs": RUN_LIST,
    "/api/forecasts/accuracy": ACCURACY,
    "/api/forecasts": FORECAST_LIST,
    "/api/relations/memories": MEMORY_LIST,
    "/api/relations": RELATION_LIST,
    "/api/collection": { items: [], since: "2026-09-03T00:00:00Z" },
    "/api/documents": { items: [], limit: 100, offset: 0, has_more: false },
  });
  renderApp("/");

  expect(await screen.findByRole("heading", { name: "대시보드" })).toBeTruthy();
});

it("정한 클라이언트 라우트가 전부 있다", async () => {
  stubFetch({
    "/api/llm-runs/9/tool-calls/1": CALL_DETAIL,
    "/api/llm-runs/9": RUN_DETAIL,
    "/api/llm-runs": RUN_LIST,
    "/api/forecasts/quality": QUALITY,
    "/api/forecasts/accuracy": ACCURACY,
    "/api/forecasts/2026-09-03/midday": FORECAST_DETAIL,
    "/api/forecasts": FORECAST_LIST,
    "/api/relations/graph": RELATION_GRAPH,
    "/api/relations/memories": MEMORY_LIST,
    "/api/relations/US10Y": OBSERVATION_LIST,
    "/api/relations": RELATION_LIST,
    "/api/documents/1": { body: null, summary: null, assessment: null, detected_at: "2026-08-27T00:00:00Z", content_hash: "a", assessed_content_hash: "a", source_slug: "cnbc", external_id: "x", title: "t", document_type: "article", published_at: "2026-08-27T00:00:00Z", content_level: "metadata_only", canonical_url: null, value_score: null, direction: null, assessed_at: null, instruments: [], indicators: [] },
    "/api/documents": { items: [], limit: 100, offset: 0, has_more: false },
    "/api/positioning": { items: [] },
    "/api/events": { items: [] },
    "/api/collection": { items: [], since: "2026-08-27T00:00:00Z" },
  });

  for (const route of CLIENT_ROUTES) {
    const path = route
      .replace(":llmRunId", "9")
      .replace(":runDate", "2026-09-03")
      .replace(":slot", "midday")
      .replace(":factor", "US10Y")
      .replace(":documentId", "1")
      .replace(":seq", "1");
    const view = renderApp(path);
    // 라우트를 등록하지 않으면 catch-all이 이 문구를 그린다.
    await waitFor(() => expect(screen.queryByText("없는 화면이다.")).toBeNull());
    view.unmount();
  }
});

it("모르는 경로는 없는 화면이라고 말한다", () => {
  stubFetch({});
  renderApp("/nope");

  expect(screen.getByText("없는 화면이다.")).toBeTruthy();
});

it("500은 재시도 버튼이 있는 오류 화면이다", async () => {
  // 이전 성공 데이터를 거짓으로 최신처럼 남기지 않는다.
  stubFetch({ "/api/llm-runs": 500 });
  renderApp("/runs");

  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeTruthy();
});

it("404는 목록으로 돌아가는 링크가 있는 화면이다", async () => {
  stubFetch({ "/api/llm-runs/9": 404 });
  renderApp("/runs/9");

  expect(await screen.findByText("실행이(가) 없다.")).toBeTruthy();
  expect(screen.getByRole("link", { name: "목록으로" })).toBeTruthy();
});

it("상단 내비게이션은 대시보드와 원자료 여섯, 추론 다섯이다", () => {
  // 원자료 여섯은 **추론이 딛고 선 것**이라 앞에 둔다.
  stubFetch({});
  renderApp("/nope");

  const nav = screen.getByRole("navigation", { name: "주요 화면" });
  expect([...nav.querySelectorAll("a")].map((link) => link.textContent)).toEqual([
    "대시보드",
    "시세",
    "지표",
    "문서",
    "수급",
    "사건",
    "수집",
    "전망",
    "관계",
    "메모",
    "실행",
    "품질",
  ]);
});
