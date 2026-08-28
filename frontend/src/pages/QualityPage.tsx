// 품질. **표를 둘로 나눈다** — 예측 품질의 판과 해설 품질의 판은 서로 독립으로 움직인다.
// 한 행에 놓으면 "판 7이 이유 지지율도 올렸다"로 읽히는데 그 손잡이는 움직인 적이 없다.
//
// 첫 판은 접근 가능한 주별 표가 기준 화면이다. 정확도 선 그래프와 차트 패키지는 넣지
// 않는다 — 표본이 쌓인 뒤 표보다 추이를 읽는 시간이 실제로 오래 걸릴 때 다시 본다.
//
// **자동 승격 루프가 아니다.** 시장 상태가 바뀌므로 누적 평균만 보고 판을 올리지 않는다.

import { useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import { integerText, numberText } from "../format";
import type { QualityResponse } from "../types";

const SLOTS = [
  "pre_open",
  "intraday_morning",
  "intraday_midday",
  "intraday_afternoon",
  "pre_close",
  "post_close",
  "post_nxt_close",
];
const HORIZONS = ["0", "1", "3", "5"];

// 이 아래면 평균 옆에 "표본 부족"을 붙인다. 기간을 자동으로 넓히지는 않는다 —
// 창이 조용히 달라지면 두 판을 같은 조건으로 비교했다고 믿을 수 없다.
const THIN_SAMPLES = 10;

function samplesText(count: number): string {
  return count < THIN_SAMPLES ? `n=${count} (표본 부족)` : `n=${count}`;
}

export default function QualityPage() {
  const [params, setParams] = useSearchParams();
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const slot = params.get("slot") ?? "";
  const subject = params.get("subject_code") ?? "";
  const horizon = params.get("horizon_days") ?? "";

  const resource = useJson<QualityResponse>(
    `/api/theses/quality${query({ from, to, slot, subject_code: subject, horizon_days: horizon })}`,
  );

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };

  return (
    <section>
      <h2>품질</h2>
      <div className="filters">
        <label>
          세션 날짜(부터, KST)
          <input type="date" value={from} onChange={(event) => set("from", event.target.value)} />
        </label>
        <label>
          세션 날짜(까지, KST)
          <input type="date" value={to} onChange={(event) => set("to", event.target.value)} />
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
        <label>
          대상 코드
          <input
            type="text"
            value={subject}
            placeholder="KOSPI"
            onChange={(event) => set("subject_code", event.target.value)}
          />
        </label>
        <label>
          지평
          <select value={horizon} onChange={(event) => set("horizon_days", event.target.value)}>
            <option value="">전부</option>
            {HORIZONS.map((value) => (
              <option key={value} value={value}>
                T+{value}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => setParams(new URLSearchParams())}>
          필터 초기화
        </button>
      </div>

      <Async resource={resource} what="품질 집계" back="/quality">
        {(data) => (
          <>
            <h3>예측 품질</h3>
            <p className="state">
              키의 모델·판은 **원 추론을 생성한 실행의 값**이다. 균등 확률 baseline은{" "}
              {numberText(data.uniform_brier)}이다.
            </p>
            {data.forecast.length === 0 ? (
              <Empty>이 조건에 채점된 추론이 없다.</Empty>
            ) : (
              <table>
                <caption>
                  Brier는 방향, 크기 오차는 폭을 잰다. **합친 종합 점수를 만들지 않는다.**
                  표본 수는 metric마다 다르다 — 결측 조건이 다르기 때문이다.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">주</th>
                    <th scope="col">지평</th>
                    <th scope="col">슬롯</th>
                    <th scope="col">모델</th>
                    <th scope="col">판</th>
                    <th scope="col">평균 Brier</th>
                    <th scope="col">Brier 표본</th>
                    <th scope="col">baseline 통과</th>
                    <th scope="col">평균 크기 오차</th>
                    <th scope="col">절대 평균</th>
                    <th scope="col">크기 표본</th>
                    <th scope="col">평균 툴</th>
                    <th scope="col">평균 결과 문자</th>
                    <th scope="col">실행 표본</th>
                  </tr>
                </thead>
                <tbody>
                  {data.forecast.map((row) => (
                    <tr
                      key={`${row.week_start}:${row.horizon_days}:${row.run_slot}:${row.llm_model}:${row.prompt_version}`}
                    >
                      <td>{row.week_start}</td>
                      <td>T+{row.horizon_days}</td>
                      <td>{row.run_slot}</td>
                      <td>{row.llm_model}</td>
                      <td>{row.prompt_version}</td>
                      <td>{numberText(row.mean_brier)}</td>
                      <td>{samplesText(row.brier_samples)}</td>
                      <td>
                        {row.beats_uniform === null ? "—" : row.beats_uniform ? "통과" : "미달"}
                      </td>
                      <td>{numberText(row.mean_return_error_pct, 2)}</td>
                      <td>{numberText(row.mae_return_pct, 2)}</td>
                      <td>{samplesText(row.return_samples)}</td>
                      <td>{numberText(row.mean_tool_calls, 1)}</td>
                      <td>{row.mean_tool_result_chars === null ? "—" : integerText(Math.round(row.mean_tool_result_chars))}</td>
                      <td>{samplesText(row.run_samples)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <h3>해설 품질</h3>
            <p className="state">
              `verdict`는 **사후 해설 LLM이 내린 판정**이라 만든 판이 다르다. 원 추론의 판을
              올려도 이 표의 행은 갈라지지 않는다. 슬롯이 키에 없는 것은 해설이 슬롯이 아니라
              지평으로 갈리기 때문이다.
            </p>
            {data.narrative.length === 0 ? (
              <Empty>이 조건에 판정이 붙은 해설이 없다.</Empty>
            ) : (
              <table>
                <caption>이유가 이후 보도로 지지됐는지의 분포. 예측 품질과 합치지 않는다.</caption>
                <thead>
                  <tr>
                    <th scope="col">주</th>
                    <th scope="col">지평</th>
                    <th scope="col">모델</th>
                    <th scope="col">판</th>
                    <th scope="col">supported</th>
                    <th scope="col">contradicted</th>
                    <th scope="col">unresolved</th>
                    <th scope="col">판정 표본</th>
                  </tr>
                </thead>
                <tbody>
                  {data.narrative.map((row) => (
                    <tr key={`${row.week_start}:${row.horizon_days}:${row.llm_model}:${row.prompt_version}`}>
                      <td>{row.week_start}</td>
                      <td>T+{row.horizon_days}</td>
                      <td>{row.llm_model}</td>
                      <td>{row.prompt_version}</td>
                      <td>{row.supported}</td>
                      <td>{row.contradicted}</td>
                      <td>{row.unresolved}</td>
                      <td>{samplesText(row.verdict_samples)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </Async>
    </section>
  );
}
