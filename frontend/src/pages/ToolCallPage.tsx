// 툴 호출 하나. **결과 전문을 가져오는 유일한 화면이다.**
//
// `arguments`는 모델이 보낸 값, `validated_arguments`는 검증·기본값 적용 뒤 실제 함수에
// 들어간 값이다. 나란히 보이는 것이 요점이다 — 모델이 무엇을 요청했고 함수가 무엇을
// 받았는지가 다르면 그 차이가 곧 원인이다.

import { Link, useParams } from "react-router-dom";

import { useJson } from "../api";
import { Async } from "../components/AsyncState";
import { durationText, integerText, jsonText, kstText, prettyJson } from "../format";
import type { ToolCallDetail } from "../types";

export default function ToolCallPage() {
  const { llmRunId, seq } = useParams();
  const current = Number(seq);
  const resource = useJson<ToolCallDetail>(`/api/llm-runs/${llmRunId}/tool-calls/${seq}`);

  return (
    <Async resource={resource} what="툴 호출" back={`/runs/${llmRunId}`}>
      {(call) => (
        <section>
          <h2>
            {call.tool_name} · seq {call.seq}
          </h2>
          <nav className="pager" aria-label="호출 이동">
            <Link to={`/runs/${llmRunId}/tool-calls/${current - 1}`}>이전 seq</Link>
            <Link to={`/runs/${llmRunId}/tool-calls/${current + 1}`}>다음 seq</Link>
            <Link to={`/runs/${llmRunId}`}>실행 상세로</Link>
          </nav>

          <div className="panel">
            <dl className="meta">
              <dt>라운드</dt>
              <dd>{call.round_no}</dd>
              <dt>tool_call_id</dt>
              <dd>{call.tool_call_id}</dd>
              <dt>요청(KST)</dt>
              <dd>
                <time dateTime={call.requested_at}>{kstText(call.requested_at)}</time>
              </dd>
              <dt>소요</dt>
              <dd>{durationText(call.duration_ms)}</dd>
              <dt>결과 문자</dt>
              <dd>{integerText(call.result_chars)}</dd>
            </dl>
            {!call.delivered && call.error === null && (
              <p className="warn">
                실행됐지만 모델에게 전달되지 않음 — 결과는 진짜이고 모델만 못 봤다. 모델이 본
                입력으로 읽으면 안 된다.
              </p>
            )}
          </div>

          <h3>모델이 보낸 인자</h3>
          <pre>
            <code>{jsonText(call.arguments)}</code>
          </pre>

          <h3>검증 뒤 실제 인자</h3>
          {call.validated_arguments === null ? (
            <p className="state">
              함수에 진입하지 못했다(unknown tool 또는 인자 검증 실패). 그래서 이 칸이 비어 있다.
            </p>
          ) : (
            <pre>
              <code>{jsonText(call.validated_arguments)}</code>
            </pre>
          )}

          {call.error === null ? (
            <>
              <h3>결과</h3>
              <pre>
                <code>{prettyJson(call.result)}</code>
              </pre>
            </>
          ) : (
            <>
              <h3>오류</h3>
              <p>종류: {call.error_kind ?? "unknown"}</p>
              <pre>
                <code>{call.error}</code>
              </pre>
            </>
          )}
        </section>
      )}
    </Async>
  );
}
