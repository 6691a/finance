import { describe, expect, it } from "vitest";

import { GRAPH } from "./fixtures";
import { edgeId, elementsOf, filterElements, nodeLabel, nodeType } from "./graph";

describe("elementsOf", () => {
  it("입력을 바꾸지 않는다", () => {
    const before = JSON.stringify(GRAPH);
    elementsOf(GRAPH);
    expect(JSON.stringify(GRAPH)).toBe(before);
  });

  it("API node id를 그대로 보존한다", () => {
    // `Evidence` 노드 id가 `evidence_ref` 그 자체다. 프런트가 새 id를 만들면 상세와 못 잇는다.
    const ids = elementsOf(GRAPH)
      .filter((element) => element.group === "nodes")
      .map((element) => element.data["id"]);

    expect(ids).toEqual(["thesis:1", "thesis:2", "document:4471", "mystery:1"]);
  });

  it("edge id를 type:start:end로 만든다", () => {
    // API edge에는 id가 없다. 원 판단 CITES와 precedent pair가 유일하다는 계약 위에서 만든다.
    expect(edgeId(GRAPH.edges[0]!)).toBe("CITES:thesis:1:document:4471");
    const ids = elementsOf(GRAPH)
      .filter((element) => element.group === "edges")
      .map((element) => element.data["id"]);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("중심 노드에 표시를 남긴다", () => {
    const center = elementsOf(GRAPH).find((element) => element.data["id"] === "thesis:1");

    expect(center?.data["center"]).toBe(true);
    expect(center?.classes).toContain("center");
  });

  it("모르는 label과 edge type은 회색 기본으로 떨어지고 죽지 않는다", () => {
    const elements = elementsOf(GRAPH);
    const mystery = elements.find((element) => element.data["id"] === "mystery:1");
    const whisper = elements.find((element) => element.data["type"] === "WHISPERS");

    expect(mystery?.data["nodeType"]).toBe("other");
    expect(whisper?.data["edgeType"]).toBe("other");
    // 원본 type은 버리지 않는다. 화면이 그것을 그대로 보인다.
    expect(whisper?.data["type"]).toBe("WHISPERS");
  });

  it("한쪽 끝이 없는 엣지는 버린다", () => {
    // Cytoscape는 없는 끝을 만나면 예외를 던진다.
    const broken = {
      ...GRAPH,
      edges: [{ type: "CITES", start: "thesis:1", end: "document:9999", properties: {} }],
    };

    expect(elementsOf(broken).filter((element) => element.group === "edges")).toEqual([]);
  });

  it("서버 properties를 style key로 그대로 쓰지 않는다", () => {
    const node = elementsOf(GRAPH).find((element) => element.data["id"] === "thesis:1");

    // style이 보는 것은 우리가 만든 칸이고, 원본은 `properties` 안에 통째로 남는다.
    expect(node?.data["nodeType"]).toBe("thesis");
    expect((node?.data["properties"] as Record<string, unknown>)["brier_score"]).toBe(0.51);
  });
});

describe("nodeLabel", () => {
  it("판단은 이름·날짜·슬롯을 쓴다", () => {
    expect(nodeLabel(GRAPH.nodes[0]!)).toBe("코스피\n2026-08-26\nintraday_midday");
  });

  it("근거는 제목을 쓴다", () => {
    expect(nodeLabel(GRAPH.nodes[2]!)).toBe("반도체 수출 증가");
  });

  it("쓸 이름이 없으면 id로 떨어진다", () => {
    // 빈 원을 그리지 않는다.
    expect(nodeLabel(GRAPH.nodes[3]!)).toBe("mystery:1");
    expect(nodeType(GRAPH.nodes[3]!.labels)).toBe("other");
  });
});

describe("filterElements", () => {
  it("노드를 끄면 그 노드에 붙은 엣지도 함께 사라진다", () => {
    const shown = filterElements(elementsOf(GRAPH), {
      nodeTypes: ["thesis"],
      edgeTypes: ["cites", "informed_by", "other"],
    });

    expect(shown.filter((element) => element.group === "nodes").map((element) => element.data["id"])).toEqual([
      "thesis:1",
      "thesis:2",
    ]);
    expect(shown.filter((element) => element.group === "edges").map((element) => element.data["type"])).toEqual([
      "INFORMED_BY",
    ]);
  });

  it("엣지 종류만 끄면 노드는 남는다", () => {
    const shown = filterElements(elementsOf(GRAPH), {
      nodeTypes: ["thesis", "evidence", "other"],
      edgeTypes: ["informed_by"],
    });

    expect(shown.filter((element) => element.group === "nodes").length).toBe(4);
    expect(shown.filter((element) => element.group === "edges").length).toBe(1);
  });
});
