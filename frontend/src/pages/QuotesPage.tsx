// 수집 중인 심볼 목록. **행 수와 마지막 시각이 최신성이다** — 마스터에만 있고 0건인
// 심볼이 실제로 있어서(`rate`가 그렇다), 목록이 그것을 숨기면 화면이 빈 기간을 고른다.

import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import { integerText, kstText } from "../format";
import type { QuoteSymbolItem, QuoteSymbolList } from "../types";

const KINDS = [
  "index",
  "index_future",
  "equity",
  "fx",
  "commodity",
  "crypto",
  "rate",
  "bond_future",
];

const KIND_NAMES: Record<string, string> = {
  index: "지수",
  index_future: "지수선물",
  equity: "종목",
  fx: "환율",
  commodity: "원자재",
  crypto: "크립토",
  rate: "금리",
  bond_future: "채권선물",
};

export function kindName(kind: string): string {
  return KIND_NAMES[kind] ?? kind;
}

/** 상세로 가는 경로. 종목은 거래소가 필요하므로 있으면 첫 번째를 미리 얹는다. */
export function detailPath(item: QuoteSymbolItem): string {
  const exchange = item.exchanges[0];
  return exchange === undefined
    ? `/quotes/${item.kind}/${item.symbol}`
    : `/quotes/${item.kind}/${item.symbol}?exchange=${exchange}`;
}

export default function QuotesPage() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const country = params.get("country") ?? "";

  const resource = useJson<QuoteSymbolList>("/api/quotes/symbols");

  const items = useMemo(() => {
    const all = resource.data?.items ?? [];
    return all.filter(
      (item) => (!kind || item.kind === kind) && (!country || item.country === country),
    );
  }, [resource.data, kind, country]);

  const countries = useMemo(
    () => [...new Set((resource.data?.items ?? []).map((item) => item.country))].sort(),
    [resource.data],
  );

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };

  return (
    <section>
      <h2>시세</h2>
      <p className="state">
        수집 중인 심볼과 **실제로 쌓인 구간**이다. 행 수가 0이면 마스터에만 있고 수집이 안
        돌고 있다는 뜻이다.
      </p>
      <div className="filters">
        <label>
          종류
          <select value={kind} onChange={(event) => set("kind", event.target.value)}>
            <option value="">전부</option>
            {KINDS.map((value) => (
              <option key={value} value={value}>
                {kindName(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          국가
          <select value={country} onChange={(event) => set("country", event.target.value)}>
            <option value="">전부</option>
            {countries.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Async resource={resource} what="심볼 목록" back="/quotes">
        {() =>
          items.length === 0 ? (
            <Empty>이 조건에 심볼이 없다.</Empty>
          ) : (
            <table>
              <caption>분봉과 일봉은 축이 다르다 — 분봉은 UTC 시각, 일봉은 그 시장의 거래일이다.</caption>
              <thead>
                <tr>
                  <th scope="col">종류</th>
                  <th scope="col">심볼</th>
                  <th scope="col">이름</th>
                  <th scope="col">국가</th>
                  <th scope="col">제공처</th>
                  <th scope="col">거래소</th>
                  <th scope="col">분봉</th>
                  <th scope="col">마지막 분봉(KST)</th>
                  <th scope="col">일봉</th>
                  <th scope="col">거래일 구간</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={`${item.kind}:${item.symbol}`}>
                    <td>{kindName(item.kind)}</td>
                    <td>
                      <Link to={detailPath(item)}>{item.symbol}</Link>
                    </td>
                    <td>{item.label}</td>
                    <td>{item.country}</td>
                    <td>{item.provider}</td>
                    <td>{item.exchanges.join(" · ") || "—"}</td>
                    <td>{integerText(item.bar_rows)}</td>
                    <td>
                      {item.bar_to === null ? (
                        "—"
                      ) : (
                        <time dateTime={item.bar_to}>{kstText(item.bar_to)}</time>
                      )}
                    </td>
                    <td>{integerText(item.daily_rows)}</td>
                    <td>
                      {item.daily_from === null ? "—" : `${item.daily_from} → ${item.daily_to}`}
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
