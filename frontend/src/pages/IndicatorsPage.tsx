// 지표 시계열 목록.
//
// **`kind`가 필터의 첫 칸이다.** 단위가 다른 값(국채 %, 물가지수 지수, 실물활동 백만 달러,
// 대차대조표 잔액)이 한 목록에 섞여 있어, 종류를 안 좁히면 무엇과 무엇을 견주는지가 흐려진다.
//
// **목록은 서버가 준 종류를 따라간다.** 아래 상수는 라벨(한국어 이름)일 뿐이고, 여기 없는
// 종류가 오면 그 값 자체를 보인다 — 수집이 종류를 늘릴 때 화면이 그것을 숨기면 안 된다.

import { Link, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import Pager, { pageOf } from "../components/Pager";
import { integerText } from "../format";
import type { IndicatorSeriesList } from "../types";

const KINDS = [
  { id: "government_bond", label: "국채" },
  { id: "money_market", label: "단기 자금시장" },
  // 정책금리는 중앙은행이 정하는 값이고 CD 91일은 시장이 만드는 값이라 한 축에 안 놓는다.
  { id: "policy_rate", label: "정책금리" },
  // 실질금리와 기대인플레. 만기가 국채와 같아 국채에 넣으면 10년물이 둘로 보인다.
  { id: "tips_rate", label: "실질금리·기대인플레" },
  { id: "credit_spread", label: "신용스프레드" },
  { id: "price_index", label: "물가지수" },
  { id: "activity", label: "실물활동" },
  // 잔액은 가격이 아니라 수량이라 통화별 단위(백만 달러·억엔)로 저장한다.
  { id: "balance_sheet", label: "중앙은행 총자산" },
  { id: "balance_sheet_item", label: "중앙은행 항목" },
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
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const resource = useJson<IndicatorSeriesList>(
    `/api/indicators/series${query({ kind, country, offset: offset || undefined })}`,
  );

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    // 필터를 바꾸면 첫 쪽으로 간다. 안 그러면 결과가 한 쪽뿐인 조건에서 빈 쪽이 보인다.
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
            <>
              <table>
                <caption>
                  단위는 정규화한 표기다 — 제공처가 `연%`든 `Percent`든 같은 값으로 맞춘다. 만기가
                  `—`면 만기 개념이 없는 지표이고 0이 아니다.
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
              <Pager page={pageOf(data)} onMove={move} />
            </>
          )
        }
      </Async>
    </section>
  );
}
