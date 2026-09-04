// 전망 하나. **이유 하나하나가 근거로 되짚어진다.**
//
// 옛 추론 화면과 가장 다른 점이 그것이다 — `factor`는 관계 화면으로, `memory_id`는 메모로,
// `slot_ref`는 같은 날 그 슬롯으로 간다. 근거가 링크가 아니면 "그렇게 말했다"에서 끝난다.
//
// **`input_state`는 표로 그리지 않는다.** 그 모양은 프롬프트 판마다 바뀌고, 화면이 모양을
// 알면 판이 오를 때마다 화면이 깨진다. 접어 두고 펼치면 JSON 그대로 보인다.

import { Link, useParams } from "react-router-dom";

import { useJson } from "../api";
import { Async } from "../components/AsyncState";
import {
  bandText,
  jsonText,
  kstText,
  numberText,
  signedPercentText,
} from "../format";
import { DIRECTIONS, FORECAST_SLOTS, labelOf } from "../labels";
import type { ForecastDetail, ForecastReason } from "../types";
import { gradeText } from "./ForecastPage";

/** 이유 하나가 가리키는 곳. **셋 다 없으면 관측 상태에서 직접 읽은 것이다.** */
export function reasonLink(reason: ForecastReason, runDate: string): string | null {
  if (reason.factor !== null) return `/relations/${reason.factor}`;
  if (reason.memory_id !== null) return `/memories?id=${reason.memory_id}`;
  if (reason.slot_ref !== null) return `/forecast/${runDate}/${reason.slot_ref}`;
  return null;
}

/** 그 링크에 붙일 이름. 무엇을 인용했는지가 글자로 보여야 한다. */
export function reasonSource(reason: ForecastReason): string {
  if (reason.factor !== null) return reason.factor;
  if (reason.memory_id !== null) return `메모 #${reason.memory_id}`;
  if (reason.slot_ref !== null) return labelOf(FORECAST_SLOTS, reason.slot_ref);
  return "관측 상태";
}

function Reason({ reason, runDate }: { reason: ForecastReason; runDate: string }) {
  const to = reasonLink(reason, runDate);
  const name = reasonSource(reason);
  return (
    <li>
      <span className={reason.direction === "up" ? "up" : "down"}>
        {labelOf(DIRECTIONS, reason.direction)}
      </span>{" "}
      <strong>{to === null ? name : <Link to={to}>{name}</Link>}</strong>
      <p>{reason.statement}</p>
    </li>
  );
}

export default function ForecastDetailPage() {
  const { runDate = "", slot = "" } = useParams();
  const resource = useJson<ForecastDetail>(`/api/forecasts/${runDate}/${slot}`);

  return (
    <section>
      <p className="state">
        <Link to={`/forecast?day=${runDate}`}>← 그날의 전망 셋</Link>
      </p>

      <Async resource={resource} what="전망" back="/forecast">
        {(data) => (
          <>
            <h2>
              {data.run_date} {labelOf(FORECAST_SLOTS, data.slot)}{" "}
              <span className={data.direction === "up" ? "up" : "down"}>
                {data.direction === "up" ? "▲" : "▼"}{" "}
                {bandText(data.expected_change_pct, data.band_pct)}
              </span>
              {data.weak ? <span className="warn"> ⚠ 근거 없음</span> : null}
            </h2>

            <dl className="facts">
              <dt>기준 시각</dt>
              <dd>
                <time dateTime={data.as_of_at}>{kstText(data.as_of_at)}</time>
              </dd>
              <dt>기준가</dt>
              <dd>
                {numberText(data.base_price, 2)} (
                <time dateTime={data.base_at}>{kstText(data.base_at)}</time>)
              </dd>
              <dt>여기까지</dt>
              <dd>{data.so_far_pct === null ? "—" : signedPercentText(data.so_far_pct)}</dd>
              <dt>채점</dt>
              <dd>{gradeText(data)}</dd>
              <dt>판·모델</dt>
              <dd>
                판 {data.prompt_version} · {data.llm_model}
              </dd>
              <dt>대화</dt>
              <dd>
                {data.llm_run_id === null ? (
                  "—"
                ) : (
                  <Link to={`/runs/${data.llm_run_id}`}>#{data.llm_run_id}</Link>
                )}
              </dd>
            </dl>

            <h3>이유 {data.reasons.length}건</h3>
            <p className="state">
              **순서가 곧 중요도다** — 결론에 가장 크게 작용한 것이 위다. 반대 방향 이유도
              그대로 남는다.
              {data.rejected_reasons > 0
                ? ` 검증이 버린 이유 ${data.rejected_reasons}건은 여기 없다.`
                : ""}
            </p>
            {data.reasons.length === 0 ? (
              <p className="state">이유가 하나도 저장되지 않았다.</p>
            ) : (
              <ol className="reasons">
                {data.reasons.map((reason, index) => (
                  <Reason key={index} reason={reason} runDate={data.run_date} />
                ))}
              </ol>
            )}

            <h3>모델이 본 것</h3>
            <details>
              <summary>관측 상태 (input_state)</summary>
              <p className="state">
                **관계와 메모는 그래프가 원본이라 다음 날 바뀐다.** 이 칸이 그때의 사본이다.
              </p>
              <pre>{jsonText(data.input_state)}</pre>
            </details>
          </>
        )}
      </Async>
    </section>
  );
}
