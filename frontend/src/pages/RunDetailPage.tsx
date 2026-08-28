// 실행 상세. 읽는 순서는 ① 메타데이터와 성공·실패 사유 ② 호출 타임라인 ③ 산출물이다.
//
// **툴 사이에 인과 화살표를 그리지 않는다.** 13단계가 보장하는 것은 기록된 호출 순서와
// 시각뿐이고, 같은 `round_no`의 호출은 한 모델 응답의 sibling이라 병렬일 수 있다.
// 그래서 라운드로 묶어 보이고 그 안의 `seq`는 기록 순서라고 밝힌다.

import { Link, useParams } from "react-router-dom";

import { useJson } from "../api";
import { Async } from "../components/AsyncState";
import { durationText, integerText, kstText, kstTimeText } from "../format";
import type { LlmRunDetail, ToolCallSummary } from "../types";

function rounds(calls: ToolCallSummary[]): [number, ToolCallSummary[]][] {
  const grouped = new Map<number, ToolCallSummary[]>();
  for (const call of calls) {
    const bucket = grouped.get(call.round_no);
    if (bucket) bucket.push(call);
    else grouped.set(call.round_no, [call]);
  }
  return [...grouped.entries()].sort(([a], [b]) => a - b);
}

/** 성공·오류·미전달을 **글자로** 가른다. 색은 거들 뿐이다. */
function CallState({ call }: { call: ToolCallSummary }) {
  if (call.error !== null) {
    return <span className="badge badge-bad">{`실패 · ${call.error_kind ?? "unknown"}`}</span>;
  }
  if (!call.delivered) return <span className="badge">성공 · 모델에게 전달되지 않음</span>;
  return <span className="badge badge-ok">성공</span>;
}

export default function RunDetailPage() {
  const { llmRunId } = useParams();
  const resource = useJson<LlmRunDetail>(`/api/llm-runs/${llmRunId}`);

  return (
    <Async resource={resource} what="실행" back="/runs">
      {(run) => (
        <section>
          <h2>
            실행 {run.id} · {run.kind}
          </h2>
          <div className="panel">
            <dl className="meta">
              <dt>시작(KST)</dt>
              <dd>
                <time dateTime={run.started_at}>{kstText(run.started_at)}</time>
              </dd>
              <dt>종료(KST)</dt>
              <dd>
                {run.finished_at === null ? (
                  "— (종료 미기록)"
                ) : (
                  <time dateTime={run.finished_at}>{kstText(run.finished_at)}</time>
                )}
              </dd>
              <dt>전체 소요</dt>
              <dd>{durationText(run.duration_ms)}</dd>
              <dt>상태</dt>
              <dd>{run.status}</dd>
              <dt>대상일 · 슬롯</dt>
              <dd>
                {run.run_date} · {run.run_slot}
                {run.horizon_days === null ? "" : ` · T+${run.horizon_days}`}
              </dd>
              <dt>모델 · 판 · 시도</dt>
              <dd>
                {run.llm_model} · {run.prompt_version} · {run.try_number}회차
              </dd>
              <dt>dag_run_id</dt>
              <dd>{run.dag_run_id}</dd>
              <dt>왕복 · 툴 · 결과 문자</dt>
              <dd>
                {run.tool_rounds} · {run.tool_call_count} · {integerText(run.tool_result_chars)}
              </dd>
            </dl>
            {run.status === "running" && (
              <p className="warn">
                종료를 기록하지 못한 실행이다. 지금 도는 중인지 중간에 끊긴 것인지는 이 기록만으로
                가를 수 없고, 아래 툴 기록이 전부라고 보장하지도 않는다.
              </p>
            )}
            {run.investigation_truncated && (
              <p className="warn">모델이 툴을 더 부르겠다고 했는데 왕복 상한에서 끊겼다.</p>
            )}
            {run.error !== null && <p className="warn">실패 사유: {run.error}</p>}
          </div>

          <h3>호출 타임라인</h3>
          {run.tool_calls.length === 0 ? (
            <p className="state">기록된 툴 호출이 없다.</p>
          ) : (
            rounds(run.tool_calls).map(([roundNo, calls]) => (
              <table key={roundNo}>
                <caption>
                  라운드 {roundNo} — 한 모델 응답의 호출 {calls.length}건. **`seq`는 기록 순서이고
                  인과 순서가 아니다.**
                </caption>
                <thead>
                  <tr>
                    <th scope="col">seq</th>
                    <th scope="col">툴</th>
                    <th scope="col">요청(KST)</th>
                    <th scope="col">소요</th>
                    <th scope="col">결과 문자</th>
                    <th scope="col">상태</th>
                  </tr>
                </thead>
                <tbody>
                  {calls.map((call) => (
                    <tr key={call.seq}>
                      <td>
                        <Link to={`/runs/${run.id}/tool-calls/${call.seq}`}>{call.seq}</Link>
                      </td>
                      <td>{call.tool_name}</td>
                      <td>
                        <time dateTime={call.requested_at}>{kstTimeText(call.requested_at)}</time>
                      </td>
                      <td>{durationText(call.duration_ms)}</td>
                      <td>{integerText(call.result_chars)}</td>
                      <td className="wrap">
                        <CallState call={call} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))
          )}

          <h3>산출물</h3>
          {run.produced_theses.length === 0 && run.narrated_outcomes.length === 0 ? (
            <p className="state">이 실행이 남긴 산출물이 없다.</p>
          ) : (
            <ul>
              {run.produced_theses.map((thesis) => (
                <li key={thesis.id}>
                  <Link to={`/theses/${thesis.id}`}>
                    {thesis.label} ({thesis.subject_code}) · {thesis.run_date} {thesis.run_slot}
                  </Link>
                </li>
              ))}
              {run.narrated_outcomes.map((outcome) => (
                <li key={`${outcome.thesis_id}:${outcome.horizon_days}`}>
                  <Link to={`/theses/${outcome.thesis_id}`}>
                    {outcome.label} ({outcome.subject_code}) · T+{outcome.horizon_days} ·{" "}
                    {outcome.verdict ?? "판정 없음"}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </Async>
  );
}
