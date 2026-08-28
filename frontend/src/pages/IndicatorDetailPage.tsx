// 지표 시계열 하나.
//
// **축 단위는 응답의 `unit`이 정한다.** 화면이 그것을 추측하지 않는다 — 금리 4.66에서
// 4.70으로 가는 건 "+0.86%"가 아니라 "+4bp"이고, 그 구분은 단위 표기가 있어야 선다.

import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { indicatorChartData, labelAt, lastPoint } from "../chart";
import { Async, Empty } from "../components/AsyncState";
import ChartView from "../components/ChartView";
import { integerText, numberText } from "../format";
import type { IndicatorPoints } from "../types";

const RANGES = [
  { id: "3mo", label: "3개월", days: 90 },
  { id: "1y", label: "1년", days: 365 },
  { id: "3y", label: "3년", days: 365 * 3 },
  { id: "all", label: "전체", days: 365 * 20 },
];

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

export default function IndicatorDetailPage() {
  const { provider = "", seriesId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const rangeId = params.get("range") ?? "1y";
  const range = RANGES.find((entry) => entry.id === rangeId) ?? RANGES[1]!;

  const resource = useJson<IndicatorPoints>(
    `/api/indicators/observations${query({
      provider,
      series_id: seriesId,
      from: daysAgo(range.days),
    })}`,
  );

  const chart = useMemo(
    () => (resource.data === null ? null : indicatorChartData(resource.data)),
    [resource.data],
  );

  const set = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("range", value);
    setParams(next);
  };

  return (
    <section>
      <h2>
        {seriesId} · {provider}
      </h2>
      <nav className="pager" aria-label="관련 화면">
        <Link to="/indicators">지표 목록으로</Link>
        <Link to="/indicators/curve">국채 곡선 비교</Link>
      </nav>

      <div className="filters">
        <label>
          기간
          <select value={range.id} onChange={(event) => set(event.target.value)}>
            {RANGES.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Async resource={resource} what="시계열" back="/indicators">
        {(series) => {
          const last = lastPoint(series.dates, series.values);
          return series.points === 0 ? (
            <Empty>이 구간에 관측값이 없다.</Empty>
          ) : (
            <>
              <div className="readout">
                <span className="value">{last === null ? "—" : numberText(last.value, 4)}</span>
                <span>
                  {series.unit ?? "단위 미상"} · 기준 {last?.at ?? "—"} · {series.label}
                </span>
                <span>{integerText(series.points)}점</span>
              </div>

              {chart !== null && (
                // 관측일도 주말·휴장일을 건너뛴다. 시각 축이면 그 빈 칸이 그려진다.
                <ChartView
                  data={chart.data}
                  series={[{ label: series.unit ?? series.label }]}
                  time={false}
                  xLabel={(value) => labelAt(chart.labels, value)}
                  height={340}
                />
              )}

              <h3>값</h3>
              <table>
                <caption>
                  관측일 내림차순. **기준 시간대는 제공처가 정한다** — ECOS는 KST 고시일,
                  FRED는 미국 영업일이다.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">관측일</th>
                    <th scope="col">값</th>
                  </tr>
                </thead>
                <tbody>
                  {series.dates
                    .map((at, index) => ({ at, value: series.values[index] }))
                    .reverse()
                    .slice(0, 200)
                    .map((row) => (
                      <tr key={row.at}>
                        <td>{row.at}</td>
                        <td>{numberText(row.value, 4)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </>
          );
        }}
      </Async>
    </section>
  );
}
