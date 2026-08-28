// 지표 시계열 목록.
//
// **`kind`가 필터의 첫 칸이다.** 단위가 다른 값(국채 %, 물가지수 지수, 실물활동 백만 달러)이
// 한 목록에 섞여 있어, 종류를 안 좁히면 무엇과 무엇을 견주는지가 흐려진다.

import { Link, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import { integerText } from "../format";
import type { IndicatorSeriesList } from "../types";

const KINDS = [
  { id: "government_bond", label: "국채" },
  { id: "money_market", label: "단기 자금시장" },
  { id: "price_index", label: "물가지수" },
  { id: "activity", label: "실물활동" },
];

export function maturityLabel(months: number | null): string {
  if (months === null) return "—";
  if (months < 12) return `${months}개월`;
  return months % 12 === 0 ? `${months / 12}년` : `${(months / 12).toFixed(1)}년`;
}

export default function IndicatorsPage() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const country = params.get("country") ?? "";

  const resource = useJson<IndicatorSeriesList>(`/api/indicators/series${query({ kind, country })}`);

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };

  return (
    <section>
      <h2>지표</h2>
      <nav className="pager" aria-label="관련 화면">
        <Link to="/indicators/curve">국채 곡선 비교</Link>
      </nav>
      <div className="filters">
        <label>
          종류
          <select value={kind} onChange={(event) => set("kind", event.target.value)}>
            <option value="">전부</option>
            {KINDS.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          국가
          <input
            type="text"
            value={country}
            placeholder="US · KR · XM"
            onChange={(event) => set("country", event.target.value.toUpperCase())}
          />
        </label>
      </div>

      <Async resource={resource} what="지표 목록" back="/indicators">
        {(data) =>
          data.items.length === 0 ? (
            <Empty>이 조건에 시계열이 없다.</Empty>
          ) : (
            <table>
              <caption>
                단위는 정규화한 표기다 — 제공처가 `연%`든 `Percent`든 같은 값으로 맞춘다.
                만기가 `—`면 만기 개념이 없는 지표이고 0이 아니다.
              </caption>
              <thead>
                <tr>
                  <th scope="col">종류</th>
                  <th scope="col">국가</th>
                  <th scope="col">계열</th>
                  <th scope="col">이름</th>
                  <th scope="col">만기</th>
                  <th scope="col">단위</th>
                  <th scope="col">제공처</th>
                  <th scope="col">행</th>
                  <th scope="col">관측 구간</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={`${item.provider}:${item.series_id}`}>
                    <td>{KINDS.find((entry) => entry.id === item.kind)?.label ?? item.kind}</td>
                    <td>{item.country}</td>
                    <td>
                      <Link to={`/indicators/${item.provider}/${item.series_id}`}>
                        {item.series_id}
                      </Link>
                    </td>
                    <td>{item.label}</td>
                    <td>{maturityLabel(item.maturity_months)}</td>
                    <td>{item.unit ?? "—"}</td>
                    <td>{item.provider}</td>
                    <td>{integerText(item.rows)}</td>
                    <td>
                      {item.observed_from === null
                        ? "—"
                        : `${item.observed_from} → ${item.observed_to}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Async>
    </section>
  );
}
