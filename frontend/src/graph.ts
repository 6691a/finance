// API 그래프 payload를 Cytoscape elements로. **순수 함수다** — Cytoscape를 import하지 않고
// 입력을 바꾸지도 않는다.
//
// **서버 properties를 style selector에 직접 넣지 않는다.** 여기서 허용된 style key
// (`nodeType`·`edgeType`·`direction`)만 만들고, 예상하지 못한 label이나 edge type은 회색
// 기본 모양으로 떨어뜨린다 — 새 종류가 생겼다고 화면이 죽으면 안 된다.

import type { GraphEdge, GraphNode, GraphResponse } from "./types";

/** style이 아는 노드 종류. 그 밖은 `other`다. */
export type NodeType = "thesis" | "evidence" | "other";

/** style이 아는 엣지 종류. 그 밖은 `other`다. */
export type EdgeType = "cites" | "informed_by" | "other";

export interface CytoscapeElement {
  group: "nodes" | "edges";
  data: Record<string, unknown>;
  classes?: string;
}

export function nodeType(labels: string[]): NodeType {
  if (labels.includes("Thesis")) return "thesis";
  if (labels.includes("Evidence")) return "evidence";
  return "other";
}

export function edgeType(type: string): EdgeType {
  if (type === "CITES") return "cites";
  if (type === "INFORMED_BY") return "informed_by";
  return "other";
}

/**
 * 엣지 id. **API node는 `id`를 갖지만 edge는 갖지 않는다.**
 *
 * 원 판단 `CITES`와 precedent pair가 각각 유일하다는 API 계약 아래 `type:start:end`로
 * 만든다. 그 유일성은 backend schema 테스트가 보장한다.
 */
export function edgeId(edge: GraphEdge): string {
  return `${edge.type}:${edge.start}:${edge.end}`;
}

function text(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

/** 노드에 보일 짧은 이름. 없는 칸은 id로 떨어진다 — 빈 원을 그리지 않는다. */
export function nodeLabel(node: GraphNode): string {
  const kind = nodeType(node.labels);
  if (kind === "thesis") {
    const label = text(node.properties["label"]) || text(node.properties["subject_code"]);
    const slot = text(node.properties["run_slot"]);
    const day = text(node.properties["run_date"]);
    return [label, day, slot].filter(Boolean).join("\n") || node.id;
  }
  if (kind === "evidence") {
    return text(node.properties["title"]) || node.id;
  }
  return node.id;
}

export function elementsOf(graph: GraphResponse): CytoscapeElement[] {
  const nodes: CytoscapeElement[] = graph.nodes.map((node) => ({
    group: "nodes",
    data: {
      id: node.id,
      label: nodeLabel(node),
      nodeType: nodeType(node.labels),
      // 중심 노드는 가장 크게 그린다. 1홉이라 중심이 어디인지가 곧 문맥이다.
      center: node.id === graph.center,
      labels: node.labels,
      properties: node.properties,
    },
    classes: `${nodeType(node.labels)}${node.id === graph.center ? " center" : ""}`,
  }));

  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const edges: CytoscapeElement[] = graph.edges
    // 양 끝이 다 있는 엣지만 그린다. Cytoscape는 없는 끝을 만나면 예외를 던진다.
    .filter((edge) => nodeIds.has(edge.start) && nodeIds.has(edge.end))
    .map((edge) => ({
      group: "edges",
      data: {
        id: edgeId(edge),
        source: edge.start,
        target: edge.end,
        edgeType: edgeType(edge.type),
        type: edge.type,
        direction: text(edge.properties["direction"]) || "none",
        label: edge.type === "CITES" ? `${text(edge.properties["rank"])} ${text(edge.properties["direction"])}`.trim() : edge.type,
        properties: edge.properties,
      },
      classes: `${edgeType(edge.type)} direction-${text(edge.properties["direction"]) || "none"}`,
    }));

  return [...nodes, ...edges];
}

/** 필터가 남긴 요소만. 화면의 그래프와 접근 가능한 목록이 같은 집합을 봐야 한다. */
export function filterElements(
  elements: CytoscapeElement[],
  { nodeTypes, edgeTypes }: { nodeTypes: NodeType[]; edgeTypes: EdgeType[] },
): CytoscapeElement[] {
  const nodes = elements.filter(
    (element) => element.group === "nodes" && nodeTypes.includes(element.data["nodeType"] as NodeType),
  );
  const kept = new Set(nodes.map((node) => node.data["id"] as string));
  const edges = elements.filter(
    (element) =>
      element.group === "edges" &&
      edgeTypes.includes(element.data["edgeType"] as EdgeType) &&
      kept.has(element.data["source"] as string) &&
      kept.has(element.data["target"] as string),
  );
  return [...nodes, ...edges];
}
