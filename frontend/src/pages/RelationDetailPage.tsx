// 요인 하나의 관측이 날짜순으로 쌓인다.
//
// **관찰 문장은 그 대화로 링크한다.** 어느 실행이 그 문장을 냈는지가 원장에 있고, 그것이
// "왜 이렇게 봤나"를 되짚는 유일한 경로다.
//
// **무게를 함께 보인다.** 가중치 표의 숫자 하나가 어디서 왔는지가 여기서 풀린다 — 나이가
// 반감기(5일)마다 절반이 된다.

import { Link, useParams, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import Pager, { pageOf } from "../components/Pager";
import { numberText } from "../format";
import { OBSERVATION_SIGNS, OBSERVATION_STRENGTHS, labelOf } from "../labels";
import type { ObservationList } from "../types";

export default function RelationDetailPage() {
  const { factor = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const resource = useJson<ObservationList>(
    `/api/relations/${factor}${query({ offset: offset || undefined })}`,
  );

  const move = (value: number) => {
    const next = new URLSearchParams(params);
    if (value > 0) next.set("offset", String(value));
    else next.delete("offset");
    setParams(next);
  };

  return (
    <section>
      <p className="state">
        <Link to="/relations">← 요인 관계</Link>
      </p>
      <h2>{factor}</h2>
      <p className="state">
        하루에 최대 하나씩 쌓인다. **옛 관측을 지우지 않는다** — 무게만 준다.
      </p>

      <Async resource={resource} what="관측" back="/relations">
        {(data) =>
          data.items.length === 0 ? (
            <Empty>이 요인에는 아직 관측이 없다. 관계가 없다는 뜻이 아니다.</Empty>
          ) : (
            <>
              <table>
                <caption>최신순. 무게는 오늘 기준이고 반감기 5일로 준다.</caption>
                <thead>
                  <tr>
                    <th scope="col">날짜</th>
                    <th scope="col">방향</th>
                    <th scope="col">세기</th>
                    <th scope="col">무게</th>
                    <th scope="col">관찰</th>
                    <th scope="col">대화</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <tr key={`${row.observed_on}:${row.factor}`}>
                      <td>{row.observed_on}</td>
                      <td className={row.sign === "same" ? "up" : "down"}>
                        {labelOf(OBSERVATION_SIGNS, row.sign)}
                      </td>
                      <td>{labelOf(OBSERVATION_STRENGTHS, String(row.strength))}</td>
                      <td>{numberText(row.weight, 3)}</td>
                      <td>{row.note || "—"}</td>
                      <td>
                        {row.llm_run_id === null ? (
                          "—"
                        ) : (
                          <Link to={`/runs/${row.llm_run_id}`}>#{row.llm_run_id}</Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pager page={pageOf(data)} onMove={move} />
            </>
          )
        }
      </Async>
    </section>
  );
}
