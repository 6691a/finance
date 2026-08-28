// 관계 그래프. `GET /api/theses/{id}/graph`의 스키마를 그대로 그린다 — 새 그래프 API나
// 프런트 전용 node model을 만들지 않는다.
//
// **graph canvas만으로는 스크린리더가 관계를 읽을 수 없다.** 같은 node·edge를 아래 목록으로도
// 준다. 필터를 걸면 둘이 같은 집합을 봐야 한다.
//
// 사용자가 node를 옮겨도 서버에 저장하지 않는다. 읽기 전용 탐색기다.

import { useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useJson } from "../api";
import { Async } from "../components/AsyncState";
import GraphView, { type GraphViewHandle } from "../components/GraphView";
import { type EdgeType, type NodeType, elementsOf, filterElements } from "../graph";
import { jsonText } from "../format";
import type { GraphResponse } from "../types";

const NODE_TYPES: NodeType[] = ["thesis", "evidence", "other"];
const EDGE_TYPES: EdgeType[] = ["cites", "informed_by", "other"];

const NODE_NAMES: Record<NodeType, string> = {
  thesis: "판단",
  evidence: "근거",
  other: "그 밖",
};
const EDGE_NAMES: Record<EdgeType, string> = {
  cites: "CITES(인용)",
  informed_by: "INFORMED_BY(참고)",
  other: "그 밖",
};

function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value];
}

export default function ThesisGraphPage() {
  const { thesisId } = useParams();
  const resource = useJson<GraphResponse>(`/api/theses/${thesisId}/graph`);
  const [nodeTypes, setNodeTypes] = useState<NodeType[]>(NODE_TYPES);
  const [edgeTypes, setEdgeTypes] = useState<EdgeType[]>(EDGE_TYPES);
  const [selected, setSelected] = useState<string | null>(null);
  const handle = useRef<GraphViewHandle | null>(null);

  const all = useMemo(() => (resource.data === null ? [] : elementsOf(resource.data)), [resource.data]);
  const shown = useMemo(() => filterElements(all, { nodeTypes, edgeTypes }), [all, nodeTypes, edgeTypes]);
  const picked = shown.find((element) => element.data["id"] === selected) ?? null;

  return (
    <Async resource={resource} what="관계 그래프" back="/theses">
      {(graph) => (
        <section>
          <h2>관계 그래프</h2>
          <nav className="pager" aria-label="관련 화면">
            <Link to={`/theses/${thesisId}`}>판단 상세로</Link>
          </nav>
          <p className="state">
            `CITES`는 **원 판단의 인용만**이다. 지평별 사후 인용은 판단 상세에 남는다.
          </p>

          {graph.nodes.length === 0 ? (
            // 노드가 0개면 Cytoscape instance를 만들지 않는다. 빈 canvas는 오해만 만든다.
            <p className="state">그릴 관계가 없다.</p>
          ) : (
            <>
              <div className="filters">
                <fieldset>
                  <legend>노드 종류</legend>
                  {NODE_TYPES.map((value) => (
                    <label key={value}>
                      <input
                        type="checkbox"
                        checked={nodeTypes.includes(value)}
                        onChange={() => setNodeTypes(toggle(nodeTypes, value))}
                      />
                      {NODE_NAMES[value]}
                    </label>
                  ))}
                </fieldset>
                <fieldset>
                  <legend>관계 종류</legend>
                  {EDGE_TYPES.map((value) => (
                    <label key={value}>
                      <input
                        type="checkbox"
                        checked={edgeTypes.includes(value)}
                        onChange={() => setEdgeTypes(toggle(edgeTypes, value))}
                      />
                      {EDGE_NAMES[value]}
                    </label>
                  ))}
                </fieldset>
                <button type="button" onClick={() => handle.current?.fit()}>
                  화면에 맞추기
                </button>
                <button type="button" onClick={() => handle.current?.reset()}>
                  초기화
                </button>
              </div>

              <div className="graph-layout">
                <GraphView elements={shown} selected={selected} onSelect={setSelected} handleRef={handle} />
                <aside className="panel" aria-label="선택 상세">
                  {picked === null ? (
                    <p className="state">노드나 관계를 고르면 속성이 여기 보인다.</p>
                  ) : (
                    <>
                      <h3>{String(picked.data["id"])}</h3>
                      <pre>
                        <code>{jsonText(picked.data["properties"])}</code>
                      </pre>
                    </>
                  )}
                </aside>
              </div>

              <h3>같은 관계의 목록</h3>
              <table>
                <caption>graph canvas와 같은 집합이다. 필터를 걸면 둘이 함께 줄어든다.</caption>
                <thead>
                  <tr>
                    <th scope="col">종류</th>
                    <th scope="col" className="wrap">
                      내용
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown
                    .filter((element) => element.group === "nodes")
                    .map((node) => (
                      <tr key={String(node.data["id"])}>
                        <td>{NODE_NAMES[node.data["nodeType"] as NodeType]}</td>
                        <td className="wrap">
                          <button type="button" onClick={() => setSelected(node.data["id"] as string)}>
                            {String(node.data["label"])}
                          </button>
                        </td>
                      </tr>
                    ))}
                  {shown
                    .filter((element) => element.group === "edges")
                    .map((edge) => (
                      <tr key={String(edge.data["id"])}>
                        <td>{EDGE_NAMES[edge.data["edgeType"] as EdgeType]}</td>
                        <td className="wrap">
                          <button type="button" onClick={() => setSelected(edge.data["id"] as string)}>
                            {String(edge.data["source"])} → {String(edge.data["target"])}
                          </button>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      )}
    </Async>
  );
}
