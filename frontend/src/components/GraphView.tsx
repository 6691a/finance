// Cytoscape lifecycle만 맡는다. **`react-cytoscapejs` 같은 래퍼를 쓰지 않는다** —
// Cytoscape는 framework-agnostic이라 `ref`로 container를 받고 `useEffect`에서 instance를
// 만들면 끝이다.
//
// **instance는 하나다.** elements가 바뀌면 새로 만들지 않고 그 instance를 갱신하고,
// unmount에서 `destroy()`한다. 안 그러면 페이지를 오갈 때마다 canvas와 listener가 쌓인다.
//
// 선택은 Cytoscape 내부 상태에만 두지 않고 선택된 id를 React state로 올린다 — 상세 패널과
// URL이 같은 값을 봐야 한다.

import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";

import type { CytoscapeElement } from "../graph";

// 색 외에 모양·선을 함께 쓴다. 색만으로 종류와 방향을 구분하지 않는다.
//
// **canvas는 `styles.css`의 토큰을 못 읽는다.** Cytoscape stylesheet는 CSS가 아니라
// JS 객체라 `var(--ink)`가 안 통한다. 그래서 값이 여기 한 벌 더 있고, 아래 상수가 그
// 사본이다 — 화면 팔레트를 바꾸면 여기도 함께 바꾼다.
//
// 노드는 어두운 canvas 위의 **밝은 칩**이라 라벨이 어둡다. 반대로 엣지 라벨은 canvas
// 위에 바로 얹히므로 밝은 색에 배경색 외곽선을 둘러 선과 겹쳐도 읽히게 한다.
const CANVAS = "#14161a";
const CHIP_INK = "#16181d";
const EDGE_INK = "#c8ced8";
const EDGE_LINE = "#9aa3ae";
const UP = "#4ade80";
const DOWN = "#f87171";

const STYLE: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "text-wrap": "wrap",
      "text-max-width": "150px",
      "font-size": "12px",
      "font-weight": 600,
      "line-height": 1.25,
      "text-valign": "center",
      color: CHIP_INK,
      "background-color": "#c9ced6",
      width: 44,
      height: 44,
    },
  },
  { selector: "node.thesis", style: { shape: "ellipse", "background-color": "#8fb6e8", width: 92, height: 92 } },
  // 중심 테두리는 노드 채움(밝음)과 canvas(어두움) 사이에 놓인다. 둘 다와 대비되어야 한다.
  { selector: "node.center", style: { width: 132, height: 132, "border-width": 4, "border-color": "#eef2f7" } },
  {
    selector: "node.evidence",
    style: {
      shape: "round-rectangle",
      "background-color": "#cfe3c9",
      width: 170,
      height: 56,
      "text-max-width": "156px",
    },
  },
  {
    selector: "edge",
    style: {
      label: "data(label)",
      "font-size": "11px",
      "font-weight": 600,
      color: EDGE_INK,
      "text-outline-color": CANVAS,
      "text-outline-width": 3,
      "text-rotation": "autorotate",
      width: 2,
      "line-color": EDGE_LINE,
      "target-arrow-color": EDGE_LINE,
      "target-arrow-shape": "triangle",
      "arrow-scale": 1.2,
      "curve-style": "bezier",
    },
  },
  { selector: "edge.cites", style: { "line-style": "solid" } },
  { selector: "edge.informed_by", style: { "line-style": "dashed", width: 2.5 } },
  { selector: "edge.direction-up", style: { "line-color": UP, "target-arrow-color": UP } },
  { selector: "edge.direction-down", style: { "line-color": DOWN, "target-arrow-color": DOWN } },
  { selector: ".picked", style: { "border-width": 4, "border-color": "#d9a441" } },
];

export interface GraphViewHandle {
  fit: () => void;
  reset: () => void;
}

export default function GraphView({
  elements,
  selected,
  onSelect,
  handleRef,
}: {
  elements: CytoscapeElement[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  handleRef?: { current: GraphViewHandle | null };
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const instance = useRef<cytoscape.Core | null>(null);
  // 최신 콜백을 참조로 들고 있어 listener를 다시 붙이지 않는다.
  const select = useRef(onSelect);
  select.current = onSelect;

  useEffect(() => {
    if (container.current === null) return;
    const core = cytoscape({
      container: container.current,
      style: STYLE,
      elements: [],
      // 노드를 키운 만큼 축소 여지를 넓힌다. 기본 최소 배율이 크면 큰 그래프가
      // `fit()` 뒤에도 화면 밖으로 나간다.
      minZoom: 0.15,
      maxZoom: 3,
    });
    core.on("tap", "node, edge", (event) => select.current(event.target.id() as string));
    core.on("tap", (event) => {
      if (event.target === core) select.current(null);
    });
    instance.current = core;
    if (handleRef) {
      handleRef.current = {
        fit: () => core.fit(undefined, 40),
        reset: () => {
          core.reset();
          core.fit(undefined, 40);
        },
      };
    }
    return () => {
      core.destroy();
      instance.current = null;
      if (handleRef) handleRef.current = null;
    };
  }, [handleRef]);

  useEffect(() => {
    const core = instance.current;
    if (core === null) return;
    core.elements().remove();
    core.add(elements as cytoscape.ElementDefinition[]);
    // 1홉이라 방향과 중심이 안정적으로 보이는 breadthfirst가 force layout보다 낫다.
    //
    // **root는 selector 문자열이 아니라 collection으로 준다.** 노드 id가 `thesis:1`처럼
    // 콜론을 갖고 있어서 `#id` 문자열로 주려면 매번 이스케이프해야 한다.
    const center = elements.find((element) => element.data["center"] === true);
    core
      .layout({
        name: "breadthfirst",
        directed: true,
        padding: 40,
        // 노드가 커진 만큼 층 간격과 노드 간격을 벌린다. 기본값이면 라벨이 서로 겹친다.
        spacingFactor: 1.5,
        avoidOverlap: true,
        ...(center ? { roots: core.getElementById(center.data["id"] as string) } : {}),
      })
      .run();
  }, [elements]);

  useEffect(() => {
    const core = instance.current;
    if (core === null) return;
    core.elements().removeClass("picked");
    if (selected !== null) core.getElementById(selected).addClass("picked");
  }, [selected]);

  return <div className="graph-canvas" ref={container} data-testid="graph-canvas" />;
}
