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
  source_kind: "event",
  event_id: 1,
  event_title: "미국 7월 소비자물가 상승률 둔화",
  event_occurred_on: "2026-08-12",
  source_target_kind: null,
  source_target_code: null,
  source_sign: null,
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

/** 대상에서 출발한 경로. 둘째 경로의 대상(`NASDAQ100_FUT`)이 이 경로의 원인이다. */
const FROM_TARGET = {
  ...PATH,
  id: 3,
  source_kind: "target",
  event_id: null,
  event_title: null,
  event_occurred_on: null,
  source_target_kind: "quote",
  source_target_code: "NASDAQ100_FUT",
  source_sign: "up",
  target_kind: "instrument",
  target_code: "005930",
  channels: ["이익 기대"],
  confidence: "endpoint_observed",
  sign: "up",
};

const DETAIL = {
  path: PATH,
  siblings: [PATH, SIBLING, FROM_TARGET],
  evidence: [
    {
      path_id: 1,
      ref: "document:189",
      kind: "document",
      title: "물가 둔화에 국채금리 하락",
      url: "/documents/189",
    },
    {
      path_id: 1,
      ref: "disclosure:20260826000445",
      kind: "disclosure",
      title: null,
      url: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260826000445",
    },
    // 형제의 근거는 이 경로 밑에 안 보인다.
    { path_id: 2, ref: "document:20", kind: "document", title: "다른 문서", url: "/documents/20" },
  ],
};

afterEach(() => {
  added.length = 0;
  vi.unstubAllGlobals();
});

it("그 주의 경로 전부를 한 그래프로 그리고 사슬이 이어진다", async () => {
  // 경로 하나만 그리면 직선 하나라 그림이 말해 주는 것이 없다. **대상 출발 경로는 다른
  // 경로의 대상 노드에 이어 붙어 다중 홉이 된다.**
  stubFetch({ "/api/causal/paths/1": DETAIL });
  renderAt("/causal/1", "/causal/:pathId", <CausalDetailPage />);

  await screen.findByRole("heading", { name: "인과 경로" });
  expect(screen.getByRole("heading", { name: "이 주의 경로 3개" })).toBeTruthy();

  const drawn = added.flat() as { group: string; data: { id: string } }[];
  const nodes = drawn.filter((element) => element.group === "nodes").map((e) => e.data.id);
  // `NASDAQ100_FUT`은 둘째 경로의 결과이자 셋째 경로의 원인이라 노드가 하나뿐이다.
  expect(nodes.filter((id) => id === "target:quote:NASDAQ100_FUT").length).toBe(1);
  const edges = drawn.filter((element) => element.group === "edges").map((e) => e.data.id);
  expect(edges).toContain("target:quote:NASDAQ100_FUT->channel:이익 기대");
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

it("이 경로가 든 근거만 보이고 원문으로 이어진다", async () => {
  // **`confidence`가 옳은지 되짚는 자리다.** 근거가 없으면 `함께 관찰`이 무엇에 기대고
  // 있는지 알 수 없다.
  stubFetch({ "/api/causal/paths/1": DETAIL });
  renderAt("/causal/1", "/causal/:pathId", <CausalDetailPage />);

  await screen.findByRole("heading", { name: "이 경로가 든 근거" });
  expect(screen.getByRole("link", { name: "물가 둔화에 국채금리 하락" }).getAttribute("href")).toBe(
    "/documents/189",
  );
  // 공시는 접수번호가 곧 DART 주소다. 제목이 없으면 식별자를 그대로 보인다.
  expect(
    screen.getByRole("link", { name: "disclosure:20260826000445" }).getAttribute("href"),
  ).toContain("rcpNo=20260826000445");
  // 형제 경로의 근거는 여기 없다.
  expect(screen.queryByRole("link", { name: "다른 문서" })).toBeNull();
});

it("근거가 없는 경로는 없다고 말한다", async () => {
  // 없다는 것도 사실이다. 감추면 되짚을 수 없다.
  stubFetch({ "/api/causal/paths/1": { ...DETAIL, evidence: [] } });
  renderAt("/causal/1", "/causal/:pathId", <CausalDetailPage />);

  expect(await screen.findByText("이 경로에 저장된 근거가 없다.")).toBeTruthy();
});
