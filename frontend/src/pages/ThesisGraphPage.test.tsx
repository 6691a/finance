// 관계 그래프 화면. **canvas와 접근 가능한 목록이 같은 집합을 봐야 한다** — 필터를 걸면
// 둘이 함께 줄어든다. canvas만으로는 스크린리더가 관계를 읽을 수 없다.

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { GRAPH } from "../fixtures";
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

const { default: ThesisGraphPage } = await import("./ThesisGraphPage");

afterEach(() => {
  added.length = 0;
  vi.unstubAllGlobals();
});

/** canvas에 실제로 들어간 요소의 id. 목록과 대조할 기준이다. */
function onCanvas(): string[] {
  const last = added[added.length - 1] ?? [];
  return (last as { data: { id: string } }[]).map((element) => element.data.id).sort();
}

function inList(): string[] {
  return [...document.querySelectorAll("tbody button")]
    .map((node) => node.textContent ?? "")
    .sort();
}

it("목록과 canvas가 같은 수의 요소를 본다", async () => {
  stubFetch({ "/api/theses/1/graph": GRAPH });
  renderAt("/theses/1/graph", "/theses/:thesisId/graph", <ThesisGraphPage />);

  await screen.findByRole("heading", { name: "같은 관계의 목록" });
  // 노드 넷 + 엣지 셋.
  expect(onCanvas().length).toBe(7);
  expect(inList().length).toBe(7);
});

it("노드 종류를 끄면 목록과 canvas가 함께 줄어든다", async () => {
  stubFetch({ "/api/theses/1/graph": GRAPH });
  renderAt("/theses/1/graph", "/theses/:thesisId/graph", <ThesisGraphPage />);

  await screen.findByRole("heading", { name: "같은 관계의 목록" });
  await userEvent.click(screen.getByLabelText("근거"));

  // 근거 노드와 그 CITES 엣지가 함께 사라진다.
  expect(onCanvas()).not.toContain("document:4471");
  expect(onCanvas().length).toBe(inList().length);
  expect(document.body.textContent).not.toContain("반도체 수출 증가");
});

it("모르는 관계 종류도 목록에 원본 이름으로 남는다", async () => {
  stubFetch({ "/api/theses/1/graph": GRAPH });
  renderAt("/theses/1/graph", "/theses/:thesisId/graph", <ThesisGraphPage />);

  await screen.findByRole("heading", { name: "같은 관계의 목록" });
  // 새 종류가 생겼다고 화면이 죽지 않는다.
  expect(screen.getAllByText("그 밖").length).toBeGreaterThan(0);
});

it("노드를 고르면 속성이 상세 패널에 보인다", async () => {
  stubFetch({ "/api/theses/1/graph": GRAPH });
  renderAt("/theses/1/graph", "/theses/:thesisId/graph", <ThesisGraphPage />);

  await screen.findByRole("heading", { name: "같은 관계의 목록" });
  const panel = screen.getByRole("complementary", { name: "선택 상세" });
  expect(panel.textContent).toContain("노드나 관계를 고르면");

  await userEvent.click(screen.getByRole("button", { name: /반도체 수출 증가/ }));

  expect(panel.textContent).toContain("document:4471");
});

it("그릴 관계가 없으면 Cytoscape instance를 만들지 않는다", async () => {
  stubFetch({ "/api/theses/1/graph": { center: "thesis:1", nodes: [], edges: [] } });
  renderAt("/theses/1/graph", "/theses/:thesisId/graph", <ThesisGraphPage />);

  expect(await screen.findByText("그릴 관계가 없다.")).toBeTruthy();
  expect(screen.queryByTestId("graph-canvas")).toBeNull();
  expect(added.length).toBe(0);
});
