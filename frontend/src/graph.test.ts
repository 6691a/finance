import { describe, expect, test } from "vitest";

import { edgeId, factorOf, nodeLabel, relationElements, weightClass } from "./graph";
import type { RelationGraph, RelationItem } from "./types";

function graph(overrides: Partial<RelationGraph> = {}): RelationGraph {
  return {
    as_of_date: "2026-09-03",
    nodes: [
      { id: "index:KOSPI", kind: "index", label: "코스피", n_obs: 0 },
      { id: "factor:FOREIGN_NET_BUY", kind: "factor", label: "외국인 순매수", n_obs: 12 },
      { id: "factor:VIX", kind: "factor", label: "VIX", n_obs: 0 },
    ],
    edges: [
      { source: "factor:FOREIGN_NET_BUY", target: "index:KOSPI", weight: 0.8, n_obs: 12 },
    ],
    ...overrides,
  };
}

describe("weightClass", () => {
  test("부호가 방향을 정한다", () => {
    expect(weightClass(0.8)).toBe("direction-up");
    expect(weightClass(-0.15)).toBe("direction-down");
  });

  test("0은 어느 쪽도 아니다 — 관측이 없을 때뿐이다", () => {
    expect(weightClass(0)).toBe("");
  });
});

describe("relationElements", () => {
  test("코스피가 중심이고 요인이 그것을 가리킨다", () => {
    const elements = relationElements(graph());

    const center = elements.find((element) => element.data["center"] === true);
    expect(center?.data["id"]).toBe("index:KOSPI");
    const edge = elements.find((element) => element.group === "edges");
    expect(edge?.data["source"]).toBe("factor:FOREIGN_NET_BUY");
    expect(edge?.data["target"]).toBe("index:KOSPI");
  });

  test("관측이 없는 요인도 노드로 남는다 — 숨기면 '관계 없음'으로 읽힌다", () => {
    const elements = relationElements(graph());

    const ids = elements.filter((element) => element.group === "nodes").map((n) => n.data["id"]);
    expect(ids).toContain("factor:VIX");
    expect(elements.filter((element) => element.group === "edges")).toHaveLength(1);
  });

  test("엣지 두께가 가중치 크기를 따른다", () => {
    const strong = relationElements(graph()).find((element) => element.group === "edges");
    const weak = relationElements(
      graph({ edges: [{ source: "factor:VIX", target: "index:KOSPI", weight: 0.05, n_obs: 1 }] }),
    ).find((element) => element.group === "edges");

    expect(Number(strong?.data["width"])).toBeGreaterThan(Number(weak?.data["width"]));
    // 아주 작은 값도 선으로 보여야 한다.
    expect(Number(weak?.data["width"])).toBeGreaterThan(1);
  });

  test("엣지 id는 양 끝으로 만든다 — 서버가 안 준다", () => {
    expect(edgeId({ source: "factor:VIX", target: "index:KOSPI", weight: 0, n_obs: 0 })).toBe(
      "factor:VIX->index:KOSPI",
    );
  });
});

describe("nodeLabel", () => {
  test("요인은 이름과 관측 수를 함께 보인다", () => {
    expect(nodeLabel("VIX", "factor", 3)).toBe("VIX\n3회");
  });

  test("지수는 이름 하나다", () => {
    expect(nodeLabel("코스피", "index", 0)).toBe("코스피");
  });
});

describe("factorOf", () => {
  const items: RelationItem[] = [
    {
      factor: "VIX",
      label: "VIX",
      weight: -0.33,
      n_obs: 3,
      last_date: "2026-08-26",
      last_note: "위험자산 약세",
      recent_signs: ["inverse"],
      url: "/api/relations/VIX",
    },
  ];

  test("고른 노드가 요인이면 그 행을 준다", () => {
    expect(factorOf(items, "factor:VIX")?.factor).toBe("VIX");
  });

  test("코스피 노드나 빈 선택은 행이 없다", () => {
    expect(factorOf(items, "index:KOSPI")).toBeNull();
    expect(factorOf(items, null)).toBeNull();
  });
});
