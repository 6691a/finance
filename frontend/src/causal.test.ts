// 인과 경로 → Cytoscape elements.
//
// 주제 셋: ① 채널 노드는 이름으로 공유된다 ② 지금 보는 경로만 방향 색이 붙는다
// ③ 같은 코드라도 대상 종류가 다르면 다른 노드다.

import { describe, expect, it } from "vitest";

import {
  causalElements,
  channelId,
  edgeIds,
  eventId,
  graphElements,
  sourceId,
  targetId,
  walk,
} from "./causal";
import type { CausalPathRow } from "./types";

function row(overrides: Partial<CausalPathRow> = {}): CausalPathRow {
  return {
    id: 1,
    week_start: "2026-08-10",
    source_kind: "event",
    event_id: 1,
    event_title: "미국 7월 소비자물가 상승률 둔화",
    event_occurred_on: "2026-08-12",
    source_target_kind: null,
    source_target_code: null,
    source_sign: null,
    target_kind: "quote",
    target_code: "US10Y",
    channels: ["통화정책 기대", "할인율"],
    sign: "down",
    confidence: "observed",
    reasoning: "설명",
    return_week_change: -0.06,
    return_t1_change: 0.6,
    return_t5_change: 0.9,
    return_unit: "percent",
    llm_run_id: null,
    ...overrides,
  };
}

describe("walk", () => {
  it("사건에서 대상까지의 순서다", () => {
    expect(walk(row())).toEqual([
      eventId(row()),
      channelId("통화정책 기대"),
      channelId("할인율"),
      targetId("quote", "US10Y"),
    ]);
  });

  it("채널이 없으면 사건이 대상에 바로 닿는다", () => {
    expect(walk(row({ channels: [] }))).toEqual([eventId(row()), targetId("quote", "US10Y")]);
  });
});

describe("causalElements", () => {
  it("두 경로가 같은 채널을 거치면 노드를 공유한다", () => {
    // 공유가 이 그래프의 요점이다. 경로마다 노드를 만들면 그 사실이 사라진다.
    const elements = causalElements(
      [row(), row({ id: 2, target_code: "NASDAQ100_FUT", channels: ["통화정책 기대"] })],
      null,
    );
    const nodes = elements.filter((element) => element.group === "nodes");

    expect(nodes.filter((node) => node.data["id"] === channelId("통화정책 기대")).length).toBe(1);
    // 사건 하나 + 채널 둘 + 대상 둘.
    expect(nodes.length).toBe(5);
  });

  it("대상에서 출발한 경로는 그 대상 노드에 이어 붙는다", () => {
    // **다중 홉의 전부가 이 공유다.** 출발점을 새 사건으로 만들었다면 `target:SOX`와
    // `event:SOX 상승`이 갈려 사슬이 끊긴다(2026-08-30 main 변경의 요점).
    const first = row({ id: 1, target_kind: "quote", target_code: "SOX" });
    const second = row({
      id: 2,
      source_kind: "target",
      event_id: null,
      event_title: null,
      event_occurred_on: null,
      source_target_kind: "quote",
      source_target_code: "SOX",
      source_sign: "up",
      target_kind: "instrument",
      target_code: "005930",
      channels: ["이익 기대"],
      confidence: "endpoint_observed",
    });

    expect(sourceId(second)).toBe(targetId("quote", "SOX"));

    const elements = causalElements([first, second], null);
    const ids = elements.filter((element) => element.group === "nodes").map((e) => e.data["id"]);
    // `SOX`는 한 번만 있고, 첫 경로의 결과이자 둘째 경로의 원인이다.
    expect(ids.filter((id) => id === targetId("quote", "SOX")).length).toBe(1);
    const edges = elements.filter((element) => element.group === "edges").map((e) => e.data["id"]);
    expect(edges).toContain(`${targetId("quote", "SOX")}->${channelId("이익 기대")}`);
  });

  it("같은 코드라도 대상 종류가 다르면 다른 노드다", () => {
    // `US10Y`는 시세, `KTB10Y`는 지표다. 저장소가 다르면 같은 값이 아니다.
    const elements = causalElements(
      [row({ target_code: "X" }), row({ id: 2, target_kind: "indicator", target_code: "X" })],
      null,
    );
    const targets = elements.filter((element) => String(element.data["id"]).startsWith("target:"));

    expect(targets.length).toBe(2);
  });

  it("지금 보는 경로만 방향 색이 붙는다", () => {
    const picked = row();
    const elements = causalElements([picked, row({ id: 2, channels: ["수요"] })], picked);
    const marked = elements.filter((element) => element.classes?.includes("direction-down"));

    expect(marked.map((element) => element.data["id"])).toEqual(edgeIds(picked));
  });

  it("노드마다 종류가 붙는다", () => {
    // 종류가 곧 스타일이다. **크기는 글자에 맞춰 늘어난다** — 숫자로 못 박으면 한국어
    // 이름이 길어질 때 원 밖으로 글자가 샌다(2026-08-28에 실제로 그랬다).
    const elements = causalElements([row()], null);
    const classes = elements
      .filter((element) => element.group === "nodes")
      .map((element) => element.classes);

    expect(classes).toEqual(["event", "channel", "channel", "target"]);
  });

  it("경로가 없으면 아무 것도 그리지 않는다", () => {
    // 빈 canvas는 오해만 만든다. 화면이 노드 0개를 보고 안 그린다.
    expect(causalElements([], null)).toEqual([]);
  });
});

describe("graphElements", () => {
  const graph = {
    source: "neo4j",
    week_start: "2026-08-10",
    nodes: [
      { id: "event:물가:2026-08-12", kind: "event", label: "물가 둔화" },
      { id: "channel:할인율", kind: "channel", label: "할인율" },
      { id: "target:quote:SOX", kind: "target", label: "SOX" },
      { id: "target:instrument:005930", kind: "target", label: "005930" },
    ],
    edges: [
      {
        source: "event:물가:2026-08-12",
        target: "channel:할인율",
        type: "LEADS_TO",
        path_id: 1,
        week_start: "2026-08-10",
        position: 1,
      },
      {
        source: "channel:할인율",
        target: "target:quote:SOX",
        type: "HITS",
        path_id: 1,
        week_start: "2026-08-10",
        position: null,
      },
      {
        source: "target:quote:SOX",
        target: "target:instrument:005930",
        type: "HITS",
        path_id: 9,
        week_start: "2026-08-17",
        position: null,
      },
    ],
  };

  it("노드 종류가 곧 스타일이다", () => {
    const classes = graphElements(graph, null)
      .filter((element) => element.group === "nodes")
      .map((element) => element.classes);
    expect(classes).toEqual(["event", "channel", "target", "target"]);
  });

  it("주를 넘는 엣지도 그대로 그린다", () => {
    // **이것이 그래프 DB를 들인 이유다.** 경로 응답은 그 주의 행이라 08-17 엣지가 없다.
    const ids = graphElements(graph, null)
      .filter((element) => element.group === "edges")
      .map((element) => element.data["id"]);
    expect(ids).toContain("target:quote:SOX->target:instrument:005930");
  });

  it("지금 보는 경로의 엣지만 방향 색을 받는다", () => {
    const marked = graphElements(graph, 1, "down").filter((element) =>
      element.classes?.includes("direction-down"),
    );
    expect(marked.length).toBe(2);
  });
});
