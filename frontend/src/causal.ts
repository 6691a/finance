// 인과 경로 응답 → Cytoscape elements. **순수 함수다** — Cytoscape를 import하지 않는다.
//
// **한 주가 한 판이다.** 상세가 그 주의 경로 전부를 주므로, 사건들과 대상들이 채널을 거쳐
// 이어지는 모양을 그대로 그린다. 경로 하나만 그리면 직선 하나라 그림이 말해 주는 것이 없다.
//
// **다중 홉은 노드를 공유해서 생긴다.** 대상에서 출발한 경로의 원인 노드는 다른 경로의
// 대상 노드와 **같은 id**다 — `VIX → NASDAQ100_FUT → SOX → 005930`이 그렇게 이어진다.
// 출발점을 새 사건으로 만들었다면 `target:SOX`와 `event:SOX 상승`이 갈려 사슬이 끊긴다.
//
// **채널 노드는 이름으로 공유한다.** 두 경로가 `할인율`을 함께 거쳤다는 사실이 요점이고,
// 경로마다 노드를 새로 만들면 그 공유가 사라진다.

import type { CytoscapeElement } from "./graph";
import { CAUSAL_TARGET_KINDS, labelOf } from "./labels";
import type { CausalGraph, CausalPathRow } from "./types";

/** 사건 노드 id. 한 주에 사건이 여럿이라 id에 사건 번호가 들어간다. */
export function eventId(row: CausalPathRow): string {
  return `event:${row.event_id}`;
}

/** 채널 노드 id. 이름이 곧 정체성이라 이름으로 만든다. */
export function channelId(name: string): string {
  return `channel:${name}`;
}

/** 대상 노드 id. 같은 코드라도 종류가 다르면 다른 대상이다(`US10Y`는 시세, `KTB10Y`는 지표). */
export function targetId(kind: string, code: string): string {
  return `target:${kind}:${code}`;
}

/**
 * 이 경로의 출발 노드.
 *
 * 대상 출발이면 **대상 노드와 같은 id**를 낸다 — 그 공유가 다중 홉의 전부다.
 */
export function sourceId(row: CausalPathRow): string {
  return row.source_kind === "target" && row.source_target_code !== null
    ? targetId(row.source_target_kind ?? "quote", row.source_target_code)
    : eventId(row);
}

/** 경로 하나가 지나는 노드 순서. 출발에서 대상까지다. */
export function walk(row: CausalPathRow): string[] {
  return [
    sourceId(row),
    ...row.channels.map(channelId),
    targetId(row.target_kind, row.target_code),
  ];
}

/** 이 경로의 엣지 id들. 강조와 중복 제거가 같은 키를 봐야 한다. */
export function edgeIds(row: CausalPathRow): string[] {
  const nodes = walk(row);
  return nodes.slice(0, -1).map((from, index) => `${from}->${nodes[index + 1]}`);
}

/** 대상 노드의 라벨. 코드 아래에 종류를 적어 `US10Y`가 시세인지 지표인지 밝힌다. */
function targetLabel(kind: string, code: string): string {
  return `${code}\n${labelOf(CAUSAL_TARGET_KINDS, kind)}`;
}

/**
 * 한 주의 그래프.
 *
 * `picked`는 지금 보고 있는 경로다 — 그 경로의 엣지만 방향 색을 입힌다. 전부에 색을 주면
 * 한 화면에서 위·아래가 뒤섞여 어느 것이 이 경로의 주장인지 사라진다.
 */
export function causalElements(
  paths: readonly CausalPathRow[],
  picked: CausalPathRow | null,
): CytoscapeElement[] {
  if (paths.length === 0) return [];

  const nodes = new Map<string, CytoscapeElement>();
  const edges = new Map<string, CytoscapeElement>();
  const highlighted = new Set(picked === null ? [] : edgeIds(picked));
  // 지금 경로의 출발 노드만 가운데 표시를 받는다. 한 주에 사건이 여럿이라 전부 키우면
  // 강조가 강조가 아니게 된다.
  const center = picked === null ? null : sourceId(picked);

  const putTarget = (kind: string, code: string) => {
    const id = targetId(kind, code);
    // 이미 있으면 덮어쓰지 않는다 — 같은 노드를 다른 경로가 결과로도 원인으로도 쓴다.
    if (!nodes.has(id)) {
      nodes.set(id, {
        group: "nodes",
        data: { id, label: targetLabel(kind, code) },
        classes: id === center ? "target center" : "target",
      });
    }
  };

  for (const row of paths) {
    if (row.source_kind === "event") {
      const id = eventId(row);
      nodes.set(id, {
        group: "nodes",
        data: { id, label: row.event_title ?? id },
        classes: id === center ? "event center" : "event",
      });
    } else if (row.source_target_code !== null) {
      putTarget(row.source_target_kind ?? "quote", row.source_target_code);
    }

    for (const name of row.channels) {
      nodes.set(channelId(name), {
        group: "nodes",
        data: { id: channelId(name), label: name },
        classes: "channel",
      });
    }
    putTarget(row.target_kind, row.target_code);

    const chain = walk(row);
    chain.slice(0, -1).forEach((from, index) => {
      const to = chain[index + 1]!;
      const key = `${from}->${to}`;
      // **엣지는 한 번만 만든다.** 두 경로가 같은 두 노드를 잇는 일이 흔하고, 그때
      // 겹쳐 그리면 화살표가 굵어질 뿐 새 사실이 없다.
      if (edges.has(key) && !highlighted.has(key)) return;
      edges.set(key, {
        group: "edges",
        data: { id: key, source: from, target: to, label: "" },
        classes: highlighted.has(key) ? `cites direction-${picked?.sign ?? "up"}` : "cites",
      });
    });
  }

  return [...nodes.values(), ...edges.values()];
}

/**
 * **그래프 DB가 준 투영**을 그대로 그린다.
 *
 * 경로 목록에서 조립하는 `causalElements`와 다른 점은 하나다 — 투영은 노드를 **주를 넘어**
 * 공유하므로 08-10 주에 닿은 `SOX`가 08-17 주의 원인으로 이어지는 사슬이 함께 온다.
 * 그 사슬이 Neo4j를 들인 이유이고(4단계 설계 §1), 조회가 시각 역행을 이미 걸러 준다.
 *
 * `pickedPathId`가 있으면 그 경로의 엣지만 방향 색을 받는다. 색을 전부에 주면 한 화면에서
 * 위·아래가 뒤섞여 어느 것이 이 경로의 주장인지 사라진다.
 */
export function graphElements(
  graph: CausalGraph,
  pickedPathId: number | null,
  pickedSign: string = "up",
): CytoscapeElement[] {
  const nodes = graph.nodes.map((node) => ({
    group: "nodes" as const,
    data: { id: node.id, label: node.label },
    classes: node.kind,
  }));

  const edges = new Map<string, CytoscapeElement>();
  for (const edge of graph.edges) {
    const mine = pickedPathId !== null && edge.path_id === pickedPathId;
    const key = `${edge.source}->${edge.target}`;
    // 엣지는 한 번만 그린다. 여러 경로가 같은 두 노드를 잇는 일이 흔하고, 그때 겹쳐
    // 그리면 화살표가 굵어질 뿐 새 사실이 없다. 다만 강조는 덮어쓴다.
    if (edges.has(key) && !mine) continue;
    edges.set(key, {
      group: "edges",
      data: { id: key, source: edge.source, target: edge.target, label: "" },
      classes: mine ? `cites direction-${pickedSign}` : "cites",
    });
  }

  return [...nodes, ...edges.values()];
}
