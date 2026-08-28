// 인과 경로 응답 → Cytoscape elements. **순수 함수다** — Cytoscape를 import하지 않는다.
//
// **한 사건이 한 판이다.** 상세가 형제 경로를 함께 주므로, 사건 하나에서 채널들을 거쳐
// 대상 여럿으로 갈라지는 모양을 그대로 그린다. 경로 하나만 그리면 직선 하나라 그림이
// 말해 주는 것이 없다.
//
// **채널 노드는 이름으로 공유한다.** 두 경로가 `통화정책 기대`를 함께 거쳤다는 사실이
// 이 그래프의 요점이고, 경로마다 노드를 새로 만들면 그 공유가 사라진다.

import type { CytoscapeElement } from "./graph";
import { CAUSAL_TARGET_KINDS, labelOf } from "./labels";
import type { CausalPathRow } from "./types";

export const EVENT_NODE = "event";

/** 채널 노드 id. 이름이 곧 정체성이라 이름으로 만든다. */
export function channelId(name: string): string {
  return `channel:${name}`;
}

/** 대상 노드 id. 같은 코드라도 종류가 다르면 다른 대상이다(`US10Y`는 시세, `KTB10Y`는 지표). */
export function targetId(row: CausalPathRow): string {
  return `target:${row.target_kind}:${row.target_code}`;
}

/** 경로 하나가 지나는 노드 순서. 사건에서 대상까지다. */
export function walk(row: CausalPathRow): string[] {
  return [EVENT_NODE, ...row.channels.map(channelId), targetId(row)];
}

/** 이 경로의 엣지 id들. 강조와 중복 제거가 같은 키를 봐야 한다. */
export function edgeIds(row: CausalPathRow): string[] {
  const nodes = walk(row);
  return nodes.slice(0, -1).map((from, index) => `${from}->${nodes[index + 1]}`);
}

/**
 * 사건 하나의 그래프.
 *
 * `picked`는 지금 보고 있는 경로다 — 그 경로의 엣지만 방향 색을 입힌다. 형제 전부에
 * 색을 주면 한 화면에서 위·아래가 뒤섞여 어느 것이 이 경로의 주장인지 사라진다.
 */
export function causalElements(
  siblings: readonly CausalPathRow[],
  picked: CausalPathRow | null,
): CytoscapeElement[] {
  const first = siblings[0];
  if (first === undefined) return [];

  const nodes = new Map<string, CytoscapeElement>();
  nodes.set(EVENT_NODE, {
    group: "nodes",
    data: { id: EVENT_NODE, label: first.event_title },
    classes: "event center",
  });

  const edges = new Map<string, CytoscapeElement>();
  const highlighted = new Set(picked === null ? [] : edgeIds(picked));

  for (const row of siblings) {
    for (const name of row.channels) {
      nodes.set(channelId(name), {
        group: "nodes",
        data: { id: channelId(name), label: name },
        classes: "channel",
      });
    }
    nodes.set(targetId(row), {
      group: "nodes",
      data: {
        id: targetId(row),
        label: `${row.target_code}\n${labelOf(CAUSAL_TARGET_KINDS, row.target_kind)}`,
      },
      classes: "target",
    });

    const chain = walk(row);
    chain.slice(0, -1).forEach((from, index) => {
      const to = chain[index + 1]!;
      const id = `${from}->${to}`;
      // **엣지는 한 번만 만든다.** 두 경로가 같은 두 노드를 잇는 일이 흔하고, 그때
      // 겹쳐 그리면 화살표가 굵어질 뿐 새 사실이 없다.
      if (edges.has(id) && !highlighted.has(id)) return;
      edges.set(id, {
        group: "edges",
        data: { id, source: from, target: to, label: "" },
        classes: highlighted.has(id) ? `cites direction-${picked?.sign ?? "up"}` : "cites",
      });
    });
  }

  return [...nodes.values(), ...edges.values()];
}
