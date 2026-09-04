// 지금 무엇을 기억하고 있나.
//
// 메모는 관계 엣지로 담기지 않는 것(“목요일 밤 미국 CPI 발표”)을 다음 전망이 알게 하는
// 자리다. 상한 20건, 나이 20일이고 매일 유지·삭제 판정을 받는다.
//
// **내린 것도 보인다.** `retired_on`과 이유가 남아 "무엇을 왜 지웠나"를 볼 수 있게 한
// 것이 설계 의도다 — 노드를 지우지 않는다.

import { Link, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import Pager, { pageOf } from "../components/Pager";
import { RETIRE_REASONS, labelOf } from "../labels";
import type { MemoryList } from "../types";

const VIEWS = [
  { value: "", label: "전부" },
  { value: "false", label: "활성만" },
  { value: "true", label: "내린 것만" },
];

export default function MemoriesPage() {
  const [params, setParams] = useSearchParams();
  const retired = params.get("retired") ?? "";
  const highlight = params.get("id");
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const resource = useJson<MemoryList>(
    `/api/relations/memories${query({ retired, offset: offset || undefined })}`,
  );

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("offset");
    setParams(next);
  };

  const move = (value: number) => {
    const next = new URLSearchParams(params);
    if (value > 0) next.set("offset", String(value));
    else next.delete("offset");
    setParams(next);
  };

  return (
    <section>
      <h2>메모</h2>
      <p className="state">
        관계로 담기지 않지만 다음 전망이 알아야 하는 것. **사실이 아니라 지난 관찰의
        메모다** — 활성 상한은 20건이고 나이 상한 20일이 지나면 코드가 내린다.
      </p>
      <div className="filters">
        <label>
          보기
          <select value={retired} onChange={(event) => set("retired", event.target.value)}>
            {VIEWS.map((view) => (
              <option key={view.value} value={view.value}>
                {view.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Async resource={resource} what="메모" back="/memories">
        {(data) =>
          data.items.length === 0 ? (
            <Empty>이 조건에 메모가 없다.</Empty>
          ) : (
            <>
              <table>
                <caption>최근에 만든 것이 위다. 내린 메모는 이유가 함께 남는다.</caption>
                <thead>
                  <tr>
                    <th scope="col">id</th>
                    <th scope="col">만든 날</th>
                    <th scope="col">내용</th>
                    <th scope="col">요인</th>
                    <th scope="col">검증</th>
                    <th scope="col">상태</th>
                    <th scope="col">대화</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <tr
                      key={row.id}
                      className={highlight === String(row.id) ? "picked" : undefined}
                    >
                      <td>#{row.id}</td>
                      <td>{row.created_on}</td>
                      <td>{row.text}</td>
                      <td>
                        {row.factor === null ? (
                          "—"
                        ) : (
                          <Link to={`/relations/${row.factor}`}>{row.factor}</Link>
                        )}
                      </td>
                      <td>
                        {row.verify_count}회
                        {row.unreviewed_count > 0 ? ` (빠짐 ${row.unreviewed_count})` : ""}
                      </td>
                      <td>
                        {row.retired_on === null
                          ? "활성"
                          : `${row.retired_on} · ${labelOf(RETIRE_REASONS, row.retire_reason)}`}
                      </td>
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
