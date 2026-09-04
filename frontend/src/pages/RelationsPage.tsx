// 요인이 코스피를 어떻게 움직였나. 원본은 Neo4j이고 가중치는 서버가 접어 준다.
//
// **가중치와 최근 방향을 나란히 둔다.** 가중치 하나로는 "오래 일관된 -0.5"와 "막 뒤집히는
// 중인 -0.15"가 구분되지 않는다 — 프롬프트가 둘을 함께 주는 이유가 화면에도 그대로다.
//
// **관측 0인 요인도 행이 있다.** 빈 칸은 "관계가 없다"로 읽히는데 뜻은 "아직 모른다"다.

import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import GraphView, { type GraphViewHandle } from "../components/GraphView";
import { numberText } from "../format";
import { OBSERVATION_SIGNS, labelOf } from "../labels";
import { factorOf, relationElements } from "../graph";
import type { RelationGraph, RelationItem, RelationList } from "../types";

/** 가중치 칸. **관측이 없으면 숫자를 아예 안 보인다** — 0.000은 "관계가 없다"로 읽힌다. */
export function weightText(item: RelationItem): string {
  return item.n_obs === 0 ? "—" : numberText(item.weight, 3);
}

/** 최근 부호를 사람 말로. 감쇠 없이 그대로다. */
export function signsText(signs: string[]): string {
  if (signs.length === 0) return "관측 없음";
  return signs.map((sign) => labelOf(OBSERVATION_SIGNS, sign)).join("·");
}

export default function RelationsPage() {
  const [picked, setPicked] = useState<string | null>(null);
  const handle = useRef<GraphViewHandle | null>(null);

  const relations = useJson<RelationList>("/api/relations?limit=200");
  const graph = useJson<RelationGraph>("/api/relations/graph");

  const chosen = factorOf(relations.data?.items ?? [], picked);

  return (
    <section>
      <h2>요인 관계</h2>
      <p className="state">
        장후 관찰이 하루에 요인당 엣지 하나를 남기고, **가중치는 코드가 반감기 5일로
        계산한다**(최근 15관측). 최근 방향이 가중치와 어긋나면 관계가 바뀌는 중이다.
      </p>

      <Async resource={graph} what="관계 그림" back="/relations">
        {(data) => (
          <>
            <div className="filters">
              <span className="state">기준일 {data.as_of_date}</span>
              <button type="button" onClick={() => handle.current?.fit()}>
                화면에 맞추기
              </button>
              <button type="button" onClick={() => handle.current?.reset()}>
                처음 배치로
              </button>
            </div>
            <GraphView
              elements={relationElements(data)}
              selected={picked}
              onSelect={setPicked}
              handleRef={handle}
            />
            {chosen === null ? (
              <p className="state">노드를 누르면 그 요인이 아래 표에서 강조된다.</p>
            ) : (
              <p className="state">
                고른 요인: <Link to={`/relations/${chosen.factor}`}>{chosen.label}</Link> ·
                가중치 {weightText(chosen)} · 관측 {chosen.n_obs}회
              </p>
            )}
          </>
        )}
      </Async>

      <Async resource={relations} what="요인 목록" back="/relations">
        {(data) =>
          data.items.length === 0 ? (
            <Empty>요인이 없다.</Empty>
          ) : (
            <table>
              <caption>
                |가중치| 내림차순. **관측이 없는 요인은 뒤에 두고 "관측 없음"으로 적는다.**
              </caption>
              <thead>
                <tr>
                  <th scope="col">요인</th>
                  <th scope="col">가중치</th>
                  <th scope="col">관측</th>
                  <th scope="col">최근 방향</th>
                  <th scope="col">마지막</th>
                  <th scope="col">마지막 관찰</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr
                    key={item.factor}
                    className={chosen?.factor === item.factor ? "picked" : undefined}
                  >
                    <td>
                      <Link to={`/relations/${item.factor}`}>{item.label}</Link>
                    </td>
                    <td className={item.n_obs === 0 ? undefined : item.weight >= 0 ? "up" : "down"}>
                      {weightText(item)}
                    </td>
                    <td>{item.n_obs}</td>
                    <td>{signsText(item.recent_signs)}</td>
                    <td>{item.last_date ?? "—"}</td>
                    <td>{item.last_note || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Async>
    </section>
  );
}
