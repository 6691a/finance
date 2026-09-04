// Cytoscape lifecycle. **모듈을 가짜로 만든다** — jsdom에는 canvas가 없고, 여기서 볼
// 것은 그리기가 아니라 "instance를 하나만 만들고 unmount에서 destroy() 하는가"다.

import { render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { relationElements } from "../graph";
import { RELATION_GRAPH } from "../fixtures";

const core = {
  on: vi.fn(),
  add: vi.fn(),
  remove: vi.fn(),
  destroy: vi.fn(),
  fit: vi.fn(),
  reset: vi.fn(),
  layout: vi.fn((options: { name: string; roots?: unknown; directed: boolean }) => {
    void options;
    return { run: vi.fn() };
  }),
  elements: vi.fn(() => ({ remove: vi.fn(), removeClass: vi.fn() })),
  getElementById: vi.fn(() => ({ addClass: vi.fn() })),
};
const factory = vi.fn(() => core);

vi.mock("cytoscape", () => ({ default: (...args: unknown[]) => factory(...(args as [])) }));

const { default: GraphView } = await import("./GraphView");

afterEach(() => {
  factory.mockClear();
  Object.values(core).forEach((value) => {
    if (typeof value === "function" && "mockClear" in value) value.mockClear();
  });
});

it("instance를 하나만 만들고 unmount에서 destroy 한다", () => {
  const view = render(
    <GraphView elements={relationElements(RELATION_GRAPH)} selected={null} onSelect={() => {}} />,
  );

  expect(factory).toHaveBeenCalledTimes(1);
  expect(core.destroy).not.toHaveBeenCalled();

  view.unmount();

  expect(core.destroy).toHaveBeenCalledTimes(1);
});

it("elements가 바뀌어도 instance를 새로 만들지 않는다", () => {
  const view = render(
    <GraphView elements={relationElements(RELATION_GRAPH)} selected={null} onSelect={() => {}} />,
  );
  view.rerender(
    <GraphView elements={relationElements({ ...RELATION_GRAPH, edges: [] })} selected={null} onSelect={() => {}} />,
  );

  // 페이지를 오갈 때마다 canvas와 listener가 쌓이면 안 된다.
  expect(factory).toHaveBeenCalledTimes(1);
  expect(core.add).toHaveBeenCalledTimes(2);
});

it("중심 노드를 root로 한 breadthfirst를 쓴다", () => {
  render(<GraphView elements={relationElements(RELATION_GRAPH)} selected={null} onSelect={() => {}} />);

  const options = core.layout.mock.calls[0]?.[0];
  expect(options?.name).toBe("breadthfirst");
  expect(options?.directed).toBe(true);
  // root는 selector 문자열이 아니라 collection이다 — 노드 id의 콜론을 이스케이프하지 않으려는 것이다.
  expect(core.getElementById).toHaveBeenCalledWith("index:KOSPI");
  expect(options?.roots).toBeDefined();
});

it("선택은 React state로 올라온다", () => {
  render(<GraphView elements={relationElements(RELATION_GRAPH)} selected="index:KOSPI" onSelect={() => {}} />);

  // Cytoscape 내부 상태에만 두면 상세 패널과 URL이 다른 값을 본다.
  expect(core.getElementById).toHaveBeenCalledWith("index:KOSPI");
});
