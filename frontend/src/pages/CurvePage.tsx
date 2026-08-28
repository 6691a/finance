// 국가별 국채 곡선. **x축이 시간이 아니라 만기다.**
//
// 나라마다 고시하는 만기가 다르므로 x축은 나온 만기 전부의 합집합이고, 그 나라가 고시하지
// 않는 만기는 선이 건너뛴다(`null`). 0으로 채우면 곡선이 바닥으로 꺾이고 그것을 "그 만기
// 금리가 0"으로 읽는다.
//
// **점마다 관측일이 다를 수 있다.** 나라마다 마지막 고시일이 달라서, 같은 날짜를 요구하면
// 일본 휴장일에 일본 곡선이 통째로 빈다. 그래서 표가 관측일을 함께 보인다.

import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { curveChartData, maturityText } from "../chart";
import { Async, Empty } from "../components/AsyncState";
import ChartView, { SERIES_COLORS } from "../components/ChartView";
import { numberText } from "../format";
import type { CurveResponse } from "../types";

export default function CurvePage() {
  const [params, setParams] = useSearchParams();
  const asOf = params.get("as_of") ?? "";

  const resource = useJson<CurveResponse>(`/api/indicators/curve${query({ as_of: asOf })}`);

  const chart = useMemo(
    () => (resource.data === null ? null : curveChartData(resource.data.countries)),
    [resource.data],
  );

  const set = (value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set("as_of", value);
    else next.delete("as_of");
    setParams(next);
  };

  return (
    <section>
      <h2>국채 곡선</h2>
      <nav className="pager" aria-label="관련 화면">
        <Link to="/indicators">지표 목록으로</Link>
      </nav>
      <p className="state">
        **국채이고 만기가 있는 계열만** 태운다. 단기 자금시장 금리와 물가지수는 단위가 달라
        같은 축에 올리지 않는다.
      </p>

      <div className="filters">
        <label>
          기준일
          <input type="date" value={asOf} onChange={(event) => set(event.target.value)} />
        </label>
      </div>

      <Async resource={resource} what="곡선" back="/indicators">
        {(data) =>
          data.countries.length === 0 ? (
            <Empty>이 기준일에 곡선이 없다.</Empty>
          ) : (
            <>
              {chart !== null && (
                <ChartView
                  data={chart.data}
                  series={chart.labels.map((label) => ({ label }))}
                  time={false}
                  xLabel={maturityText}
                  height={400}
                />
              )}

              <h3>값</h3>
              <table>
                <caption>
                  기준일 {data.as_of}까지의 마지막 값이다. **점마다 관측일이 다를 수 있다** —
                  나라마다 마지막 고시일이 달라서다.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">국가</th>
                    <th scope="col">계열</th>
                    <th scope="col">만기</th>
                    <th scope="col">값</th>
                    <th scope="col">단위</th>
                    <th scope="col">관측일</th>
                  </tr>
                </thead>
                <tbody>
                  {data.countries.flatMap((country, index) =>
                    country.points.map((point) => (
                      <tr key={`${country.country}:${point.series_id}`}>
                        <td>
                          {/* 색만으로 나라를 구분하지 않는다. 이름을 함께 적는다. */}
                          <span
                            aria-hidden="true"
                            style={{
                              display: "inline-block",
                              width: 8,
                              height: 8,
                              marginRight: 6,
                              borderRadius: 2,
                              background: SERIES_COLORS[index % SERIES_COLORS.length],
                            }}
                          />
                          {country.country} {country.country_name}
                        </td>
                        <td>{point.series_id}</td>
                        <td>{maturityText(point.maturity_months)}</td>
                        <td>{numberText(point.value, 4)}</td>
                        <td>{country.unit ?? "—"}</td>
                        <td>{point.observation_date}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </>
          )
        }
      </Async>
    </section>
  );
}
