// 품질. **표를 둘로 나눈다** — 전망 판과 관찰 판은 서로 독립으로 움직인다. 한 행에
// 놓으면 전망 판을 올린 효과가 관찰 쪽 변화로 읽힌다.
//
// 주별 표가 기준 화면이다. 정확도 선 그래프는 넣지 않는다 — 표본이 쌓인 뒤 표보다 추이를
// 읽는 시간이 실제로 오래 걸릴 때 다시 본다.
//
// **자동 승격 루프가 아니다.** 시장 상태가 바뀌므로 누적 평균만 보고 판을 올리지 않는다.

import { useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import { numberText, percentText } from "../format";
import { FORECAST_SLOTS, labelOf } from "../labels";
import type { QualityResponse } from "../types";

const SLOTS = ["pre_open", "midday", "pre_close"];

// 이 아래면 비율 옆에 "표본 부족"을 붙인다. 기간을 자동으로 넓히지는 않는다 —
// 창이 조용히 달라지면 두 판을 같은 조건으로 비교했다고 믿을 수 없다.
const THIN_SAMPLES = 10;

export function samplesText(count: number): string {
  return count < THIN_SAMPLES ? `n=${count} (표본 부족)` : `n=${count}`;
}

/** 폭이 오차를 덮는가. **덮지 못하면 구조적으로 못 맞히는 폭이다.** */
export function bandVerdict(band: number | null, error: number | null): string {
  if (band === null || error === null) return "—";
  return band >= error ? "덮음" : "부족";
}

export default function QualityPage() {
  const [params, setParams] = useSearchParams();
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const slot = params.get("slot") ?? "";

  const resource = useJson<QualityResponse>(`/api/forecasts/quality${query({ from, to, slot })}`);

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
                {labelOf(FORECAST_SLOTS, value)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Async resource={resource} what="품질" back="/quality">
        {(data) => (
          <>
            <h3>전망</h3>
            {data.forecast.length === 0 ? (
              <Empty>이 조건에 전망이 없다.</Empty>
            ) : (
              <table>
                <caption>
                  주 · 슬롯 · 모델 · 판이 키다. **방향이 둘뿐이라 찍기의 기대 적중률이{" "}
                  {percentText(data.coin_flip_hit_rate)}이고**, 그것을 못 넘는 판은 뜻이 없다.
                  채점 0건이면 비율이 `—`이지 0%가 아니다.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">주</th>
                    <th scope="col">슬롯</th>
                    <th scope="col">모델</th>
                    <th scope="col">판</th>
                    <th scope="col">채점</th>
                    <th scope="col">방향</th>
                    <th scope="col">찍기 대비</th>
                    <th scope="col">밴드</th>
                    <th scope="col">평균 오차</th>
                    <th scope="col">평균 폭</th>
                    <th scope="col">폭 판정</th>
                    <th scope="col">평균 기대</th>
                    <th scope="col">약한 답</th>
                    <th scope="col">버린 이유</th>
                  </tr>
                </thead>
                <tbody>
                  {data.forecast.map((row) => (
                    <tr key={`${row.week_start}:${row.slot}:${row.prompt_version}`}>
                      <td>{row.week_start}</td>
                      <td>{labelOf(FORECAST_SLOTS, row.slot)}</td>
                      <td>{row.llm_model}</td>
                      <td>{row.prompt_version}</td>
                      <td>
                        {samplesText(row.graded)}
                        {row.pending > 0 ? ` · 대기 ${row.pending}` : ""}
                      </td>
                      <td>{percentText(row.hit_rate)}</td>
                      <td className={row.beats_coin_flip ? "up" : "down"}>
                        {row.beats_coin_flip === null ? "—" : row.beats_coin_flip ? "넘음" : "못 넘음"}
                      </td>
                      <td>{percentText(row.band_rate)}</td>
                      <td>{numberText(row.mean_abs_error, 2)}</td>
                      <td>{numberText(row.mean_band_pct, 2)}</td>
                      <td className={row.mean_band_pct === null ? undefined : "state"}>
                        {bandVerdict(row.mean_band_pct, row.mean_abs_error)}
                      </td>
                      <td>{numberText(row.mean_expected_pct, 2)}</td>
                      <td className={row.weak ? "warn" : undefined}>{row.weak}</td>
                      <td className={row.rejected_reasons ? "warn" : undefined}>
                        {row.rejected_reasons}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <h3>장후 관찰</h3>
            {data.review.length === 0 ? (
              <Empty>이 조건에 관찰이 없다.</Empty>
            ) : (
              <table>
                <caption>
                  주 · 모델 · 판이 키다. 슬롯이 없다 — 관찰은 하루에 한 번이다. **거절이 0이
                  아니면 메모 상한을 치고 있다.**
                </caption>
                <thead>
                  <tr>
                    <th scope="col">주</th>
                    <th scope="col">모델</th>
                    <th scope="col">판</th>
                    <th scope="col">실행</th>
                    <th scope="col">관측</th>
                    <th scope="col">실행당 관측</th>
                    <th scope="col">메모(새로/내림/만료)</th>
                    <th scope="col">거절</th>
                    <th scope="col">버린 관찰</th>
                    <th scope="col">실행당 툴</th>
                    <th scope="col">상한 끊김</th>
                  </tr>
                </thead>
                <tbody>
                  {data.review.map((row) => (
                    <tr key={`${row.week_start}:${row.prompt_version}`}>
                      <td>{row.week_start}</td>
                      <td>{row.llm_model}</td>
                      <td>{row.prompt_version}</td>
                      <td>{samplesText(row.runs)}</td>
                      <td>{row.observations_written}</td>
                      <td>{numberText(row.mean_observations, 2)}</td>
                      <td>
                        {row.memories_written}/{row.memories_dropped}/{row.memories_expired}
                      </td>
                      <td className={row.memories_rejected ? "warn" : undefined}>
                        {row.memories_rejected}
                      </td>
                      <td className={row.rejected ? "warn" : undefined}>{row.rejected}</td>
                      <td>{numberText(row.mean_tool_calls, 2)}</td>
                      <td className={row.truncated ? "warn" : undefined}>{row.truncated}</td>
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
