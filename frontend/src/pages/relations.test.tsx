// 요인 관계와 메모 화면.
//
// 주제 셋: ① 관측 0이 "관계 없음"으로 읽히지 않는다 ② 가중치와 최근 방향이 나란히 있다
// ③ 내린 메모도 이유와 함께 보인다.

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { MEMORY_LIST, OBSERVATION_LIST, RELATION_GRAPH, RELATION_LIST } from "../fixtures";
import { renderAt, stubFetch } from "../test-harness";
import MemoriesPage from "./MemoriesPage";
import RelationDetailPage from "./RelationDetailPage";
import RelationsPage from "./RelationsPage";

// jsdom에는 canvas가 없다. 여기서 볼 것은 표이고 그리기는 `GraphView.test.tsx`가 본다.
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

const GRAPH_ROUTES = {
  "/api/relations/graph": RELATION_GRAPH,
  "/api/relations": RELATION_LIST,
};

it("관측이 없는 요인도 행이 있고 '관측 없음'이라고 적는다", async () => {
  stubFetch(GRAPH_ROUTES);
  renderAt("/relations", "/relations", <RelationsPage />);

  await screen.findByRole("table");
  const row = screen.getByRole("row", { name: /필라델피아 반도체/ });
  // 빈 칸은 "관계가 없다"로 읽힌다. 뜻은 "아직 모른다"다.
  expect(row.textContent).toContain("관측 없음");
  expect(row.textContent).not.toContain("0.000");
});

it("가중치와 최근 방향이 한 행에 함께 있다", async () => {
  stubFetch(GRAPH_ROUTES);
  renderAt("/relations", "/relations", <RelationsPage />);

  await screen.findByRole("table");
  // 가중치 하나로는 "오래 일관된 -0.5"와 "막 뒤집히는 중인 -0.15"가 안 갈린다.
  const row = screen.getByRole("row", { name: /미국 10년물/ });
  expect(row.textContent).toContain("-0.150");
  expect(row.textContent).toContain("같음·반대·반대");
});

it("요인 이름이 그 관측 목록으로 가는 링크다", async () => {
  stubFetch(GRAPH_ROUTES);
  renderAt("/relations", "/relations", <RelationsPage />);

  await screen.findByRole("table");
  expect(screen.getByRole("link", { name: "미국 10년물" }).getAttribute("href")).toBe(
    "/relations/US10Y",
  );
});

it("관측 목록은 무게와 그 관찰을 낸 대화를 함께 보인다", async () => {
  stubFetch({ "/api/relations/US10Y": OBSERVATION_LIST });
  renderAt("/relations/US10Y", "/relations/:factor", <RelationDetailPage />);

  await screen.findByRole("table");
  const row = screen.getByRole("row", { name: /금리 \+8bp/ });
  expect(row.textContent).toContain("0.500");
  expect(screen.getByRole("link", { name: "#13" }).getAttribute("href")).toBe("/runs/13");
});

it("관측이 없는 요인은 '관계가 없다는 뜻이 아니다'라고 말한다", async () => {
  stubFetch({ "/api/relations/SOX": { items: [], limit: 50, offset: 0, has_more: false } });
  renderAt("/relations/SOX", "/relations/:factor", <RelationDetailPage />);

  expect(await screen.findByText(/관계가 없다는 뜻이 아니다/)).toBeTruthy();
});

it("메모는 내린 것도 이유와 함께 보인다", async () => {
  stubFetch({ "/api/relations/memories": MEMORY_LIST });
  renderAt("/memories", "/memories", <MemoriesPage />);

  await screen.findByRole("table");
  // **노드를 지우지 않는다** — 왜 지웠는지가 남아야 한다.
  const retired = screen.getByRole("row", { name: /자사주 매입/ });
  expect(retired.textContent).toContain("모델이 내림");
  expect(screen.getByRole("row", { name: /CPI 발표/ }).textContent).toContain("활성");
});

it("메모 필터가 URL을 통해 요청까지 간다", async () => {
  const log = stubFetch({ "/api/relations/memories": MEMORY_LIST });
  renderAt("/memories", "/memories", <MemoriesPage />);
  await screen.findByRole("table");

  await userEvent.selectOptions(screen.getByLabelText("보기"), "true");

  await screen.findByRole("table");
  expect(log.paths[1]).toBe("/api/relations/memories?retired=true");
});
