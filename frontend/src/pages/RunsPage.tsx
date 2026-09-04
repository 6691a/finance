// 실행 목록. **필터는 전부 URL query string에 둔다** — 새로고침·뒤로 가기·링크 공유가
// 같은 화면을 복원한다.
//
// **대상 필터가 없다.** 대상이 코스피 하나라 가를 것이 없다.
//
// **메모 칸 일곱은 관찰(`review`) 대화에만 값이 있다.** 전망 행에 0을 그리면 "0건"과
// "해당 없음"이 같아 보이므로 `—`로 둔다.

import { Link, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import Pager, { pageOf } from "../components/Pager";
import { durationText, integerText, kstText } from "../format";
import { FORECAST_SLOTS, RUN_KINDS, labelOf } from "../labels";
import type { LlmRunItem, LlmRunList } from "../types";

const KINDS = ["forecast", "review"];
const STATUSES = ["running", "succeeded", "failed"];
const SLOTS = ["pre_open", "midday", "pre_close"];

/** 메모 원장 한 칸. **관찰 대화가 아니면 `—`다.** */
export function memoryText(run: LlmRunItem): string {
  const ledger = run.memories;
  if (ledger.written === null) return "—";
  return `+${ledger.written} 유지${ledger.kept ?? 0} 내림${ledger.dropped ?? 0}${
    ledger.rejected ? ` 거절${ledger.rejected}` : ""
  }`;
}

/** 상태를 **색으로만** 구분하지 않는다. 배지 안에 글자가 있고 색은 거들 뿐이다. */
function StatusBadge({ status, truncated }: { status: string; truncated: boolean | null }) {
  const text = status === "running" ? "종료 미기록" : status === "succeeded" ? "성공" : "실패";
  const tone = status === "succeeded" ? "badge-ok" : status === "failed" ? "badge-bad" : "";
  return (
    <>
      <span className={`badge ${tone}`}>{truncated ? `${text} · 조사 끊김` : text}</span>
    </>
  );
}

export default function RunsPage() {
  const [params, setParams] = useSearchParams();
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const kind = params.get("kind") ?? "";
  const status = params.get("status") ?? "";
  const slot = params.get("slot") ?? "";
  const offset = Number(params.get("offset") ?? 0);

  const resource = useJson<LlmRunList>(
    `/api/llm-runs${query({ from, to, kind, status, slot, offset: offset || undefined })}`,
  );

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    // 필터를 바꾸면 첫 쪽으로 돌아간다. 안 그러면 빈 쪽이 보인다.
    next.delete("offset");
    setParams(next);
  };

  const page = (value: number) => {
    const next = new URLSearchParams(params);
    if (value > 0) next.set("offset", String(value));
    else next.delete("offset");
    setParams(next);
  };

  return (
    <section>
      <h2>LLM 실행</h2>
      <div className="filters">
        <label>
          실행 시작일(부터, KST)
          <input type="date" value={from} onChange={(event) => set("from", event.target.value)} />
        </label>
        <label>
          실행 시작일(까지, KST)
          <input type="date" value={to} onChange={(event) => set("to", event.target.value)} />
        </label>
        <label>
          종류
          <select value={kind} onChange={(event) => set("kind", event.target.value)}>
            <option value="">전부</option>
            {KINDS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          상태
          <select value={status} onChange={(event) => set("status", event.target.value)}>
            <option value="">전부</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          슬롯
          <select value={slot} onChange={(event) => set("slot", event.target.value)}>
            <option value="">전부</option>
            {SLOTS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Async resource={resource} what="실행 목록" back="/runs">
        {(data) =>
          data.items.length === 0 ? (
            <Empty>이 조건에 실행이 없다.</Empty>
          ) : (
            <>
              <table>
                <caption>실행일 기준 내림차순. 시각은 KST이고 원본은 UTC다.</caption>
                <thead>
                  <tr>
                    <th scope="col">시작(KST)</th>
                    <th scope="col">대상일</th>
                    <th scope="col">종류</th>
                    <th scope="col">슬롯</th>
                    <th scope="col">모델</th>
                    <th scope="col">판</th>
                    <th scope="col">시도</th>
                    <th scope="col">상태</th>
                    <th scope="col">왕복</th>
                    <th scope="col">툴</th>
                    <th scope="col">결과 문자</th>
                    <th scope="col">소요</th>
                    <th scope="col">전망</th>
                    <th scope="col">관측</th>
                    <th scope="col">메모</th>
                    <th scope="col">버림</th>
                    <th scope="col">입력(캐시)</th>
                    <th scope="col">출력(사고)</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((run) => (
                    <tr key={run.id}>
                      <td>
                        <Link to={`/runs/${run.id}`}>
                          <time dateTime={run.started_at}>{kstText(run.started_at)}</time>
                        </Link>
                      </td>
                      <td>{run.run_date}</td>
                      <td>{labelOf(RUN_KINDS, run.kind)}</td>
                      <td>{run.slot === null ? "—" : labelOf(FORECAST_SLOTS, run.slot)}</td>
                      <td>{run.llm_model}</td>
                      <td>{run.prompt_version}</td>
                      <td>{run.try_number}</td>
                      <td>
                        <StatusBadge status={run.status} truncated={run.truncated === true} />
                      </td>
                      <td>{run.tool_rounds ?? "—"}</td>
                      <td>{run.tool_call_count ?? "—"}</td>
                      <td>{integerText(run.tool_result_chars)}</td>
                      <td>{durationText(run.duration_ms)}</td>
                      <td>{run.produced_count}</td>
                      <td>{run.observations_written ?? "—"}</td>
                      <td>{memoryText(run)}</td>
                      <td className={run.rejected ? "warn" : undefined}>{run.rejected ?? "—"}</td>
                      <td>
                        {run.prompt_tokens === null
                          ? "—"
                          : `${integerText(run.prompt_tokens)} (${integerText(run.cached_prompt_tokens)})`}
                      </td>
                      <td>
                        {run.completion_tokens === null
                          ? "—"
                          : `${integerText(run.completion_tokens)} (${integerText(run.reasoning_tokens)})`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pager page={pageOf(data)} onMove={page} />
            </>
          )
        }
      </Async>
    </section>
  );
}
