// 관계 응답을 Cytoscape elements로. **순수 함수다** — Cytoscape를 import하지 않고
// 입력을 바꾸지도 않는다.
//
// **서버 값을 style selector에 직접 넣지 않는다.** 여기서 허용된 class만 만들고, 예상하지
// 못한 값은 기본 모양으로 떨어뜨린다 — 새 요인이 생겼다고 화면이 죽으면 안 된다.
//
// 그림은 **깊이 1의 별 모양**이다. 요인이 코스피 하나를 가리킨다. 옛 인과 그래프처럼
// 다중 홉이 아니라서 레이아웃 고민이 없다.

import type { RelationEdge, RelationGraph, RelationItem } from "./types";

export interface CytoscapeElement {
  group: "nodes" | "edges";
  data: Record<string, unknown>;
  classes?: string;
}

/** 엣지 id. 서버가 안 주므로 양 끝으로 만든다 — 요인 하나가 코스피를 한 번만 가리킨다. */
export function edgeId(edge: RelationEdge): string {
  return `${edge.source}->${edge.target}`;
}

/**
 * 엣지의 방향 class. **`same`이 아니라 부호로 가른다** — 가중치가 이미 부호를 갖고 있다.
 *
 * 0은 관측이 없을 때뿐이고 그때는 엣지가 아예 없다. 그래도 0이 오면 회색으로 둔다.
 */
export function weightClass(weight: number): "direction-up" | "direction-down" | "" {
  if (weight > 0) return "direction-up";
  if (weight < 0) return "direction-down";
  return "";
}

/** 노드에 보일 이름. 요인은 한국어 이름과 관측 수, 코스피는 이름 하나다. */
export function nodeLabel(label: string, kind: string, observations: number): string {
  return kind === "factor" ? `${label}\n${observations}회` : label;
}

/**
 * 관계 그림의 elements.
 *
 * **관측이 없는 요인도 노드로 그린다.** 숨기면 "관계가 없다"와 "아직 모른다"가 같아
 * 보인다 — 엣지가 없는 것으로 드러낸다.
 */
export function relationElements(graph: RelationGraph): CytoscapeElement[] {
  const nodes: CytoscapeElement[] = graph.nodes.map((node) => ({
    group: "nodes",
    data: {
      id: node.id,
      label: nodeLabel(node.label, node.kind, node.n_obs),
      center: node.kind === "index",
    },
    classes: node.kind === "index" ? "target center" : "channel",
  }));

  const edges: CytoscapeElement[] = graph.edges.map((edge) => ({
    group: "edges",
    data: {
      id: edgeId(edge),
      source: edge.source,
      target: edge.target,
      label: edge.weight.toFixed(2),
      // 두께는 |가중치|에 비례한다. 최소 1.5는 둬야 0.05짜리도 선으로 보인다.
      width: 1.5 + Math.abs(edge.weight) * 5,
    },
    classes: ["cites", weightClass(edge.weight)].filter(Boolean).join(" "),
  }));

  return [...nodes, ...edges];
}

/** 요인 목록에서 그 요인 하나를 찾는다. 그림에서 고른 노드와 표를 잇는 자리다. */
export function factorOf(items: RelationItem[], nodeId: string | null): RelationItem | null {
  if (nodeId === null || !nodeId.startsWith("factor:")) return null;
  const code = nodeId.slice("factor:".length);
  return items.find((item) => item.factor === code) ?? null;
}
