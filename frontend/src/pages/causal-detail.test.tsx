// 인과 경로 상세. **canvas와 접근 가능한 표가 같은 집합을 봐야 한다** —
// canvas만으로는 스크린리더가 관계를 읽을 수 없다.

import { screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { renderAt, stubFetch } from "../test-harness";

// jsdom에는 canvas가 없다. lifecycle은 `components/GraphView.test.tsx`가 본다.
const added: unknown[][] = [];
vi.mock("cytoscape", () => ({
  default: () => ({
    on: vi.fn(),
    add: (elements: unknown[]) => added.push(elements),
    destroy: vi.fn(),
    fit: vi.fn(),
    reset: vi.fn(),
    layout: () => ({ run: vi.fn() }),
    elements: () => ({ remove: vi.fn(), removeClass: vi.fn() }),
    getElementById: () => ({ addClass: vi.fn() }),
  }),
}));

const { default: CausalDetailPage } = await import("./CausalDetailPage");

const PATH = {
  id: 1,
  week_start: "2026-08-10",
  event_id: 1,
  event_title: "미국 7월 소비자물가 상승률 둔화",
  event_occurred_on: "2026-08-12",
  target_kind: "quote",
  target_code: "US10Y",
  channels: ["통화정책 기대", "할인율"],
  sign: "down",
  confidence: "observed",
  reasoning: "물가 둔화로 긴축 우려가 낮아졌다.",
  return_week_change: -0.0638,
  return_t1_change: 0.5962,
  return_t5_change: 0.8944,
  return_unit: "percent",
  llm_run_id: null,
};

const SIBLING = {
  ...PATH,
  id: 2,
  target_code: "NASDAQ100_FUT",
  channels: ["통화정책 기대", "할인율", "밸류에이션"],
  sign: "up",
};

const DETAIL = { path: PATH, siblings: [PATH, SIBLING] };

afterEach(() => {
  added.length = 0;
  vi.unstubAllGlobals();
});

it("그 사건의 경로 전부를 한 그래프로 그린다", async () => {
  // 경로 하나만 그리면 직선 하나라 그림이 말해 주는 것이 없다.
  stubFetch({ "/api/causal/paths/1": DETAIL });
  renderAt("/causal/1", "/causal/:pathId", <CausalDetailPage />);

  await screen.findByRole("heading", { name: "인과 경로" });
  expect(screen.getByRole("heading", { name: "이 사건의 경로 2개" })).toBeTruthy();

  // 사건 하나 + 채널 셋 + 대상 둘. 채널은 이름으로 공유된다.
  const nodes = added.flat().filter((element) => (element as { group: string }).group === "nodes");
  expect(nodes.length).toBe(6);
});

it("같은 내용을 표로도 준다", async () => {
  stubFetch({ "/api/causal/paths/1": DETAIL });
  renderAt("/causal/1", "/causal/:pathId", <CausalDetailPage />);

  await screen.findByRole("table");
  // 형제 경로는 눌러서 그쪽 강조로 넘어갈 수 있다.
  expect(screen.getAllByRole("link", { name: /NASDAQ100_FUT/ }).length).toBeGreaterThan(0);
  expect(document.body.textContent).toContain("인과의 증명이 아니다");
});

it("없는 경로는 목록으로 돌아가는 길을 준다", async () => {
  stubFetch({ "/api/causal/paths/999": 404 });
  renderAt("/causal/999", "/causal/:pathId", <CausalDetailPage />);

  expect(await screen.findByText("인과 경로이(가) 없다.")).toBeTruthy();
});
