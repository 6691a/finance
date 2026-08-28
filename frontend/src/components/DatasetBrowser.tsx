// 데이터셋 여럿을 탭으로 오가며 표로 읽는 화면. 수급·사건·수집 셋이 이것을 쓴다.
//
// **한 도메인이 테이블 아홉을 갖는데 화면을 아홉 개 만들지 않는다.** 그 아홉은 전부 같은
// 질문("이 기간에 이 종목의 이 값")이고 다른 것은 열 목록뿐이라, 열 목록을 데이터로 두고
// 화면 하나가 그린다.
//
// 고른 데이터셋과 필터는 **전부 URL query string에 둔다** — 새로고침·뒤로 가기·링크
// 공유가 같은 화면을 복원한다.

import { useSearchParams } from "react-router-dom";

import { useJson } from "../api";
import { type StockNames, useStockNames } from "../stocks";
import { Async } from "./AsyncState";
import DataTable, { type Column } from "./DataTable";
import Pager, { pageOf } from "./Pager";

/** 데이터셋이 요구하는 필터. 없는 것은 화면에 안 그린다. */
export type FilterKind = "dates" | "stock" | "market" | "day";

export interface DatasetTable {
  caption: string;
  columns: Column<never>[];
  rows: unknown[];
  empty: string;
}

export interface Dataset {
  id: string;
  label: string;
  /** 이 데이터셋이 무엇인지 한 줄. 표 위에 그린다. */
  note?: string;
  filters: FilterKind[];
  /** 지금 필터로 부를 경로. */
  path: (params: URLSearchParams) => string;
  /**
   * 응답을 표 하나 이상으로. 대차거래처럼 배열이 둘인 응답이 있다.
   *
   * `names`는 종목코드 → 이름이다. 열 정의가 모듈 상수라 hook을 직접 못 부르므로
   * 여기로 흘려 준다 — 표가 `005930` 대신 `삼성전자(005930)`를 찍게 하는 통로다.
   */
  tables: (data: never, names: StockNames) => DatasetTable[];
  /** 응답이 고를 수 있는 값을 함께 줄 때(융자 순위의 기준일). */
  choices?: (data: never) => { name: string; values: string[] } | null;
}

/** 기간 프리셋. 데이터셋마다 행 밀도가 달라 기본을 다르게 준다. */
export function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

export default function DatasetBrowser({
  title,
  note,
  datasets,
  defaultDays,
}: {
  title: string;
  note?: string;
  datasets: Dataset[];
  defaultDays: number;
}) {
  const [params, setParams] = useSearchParams();
  const active = datasets.find((entry) => entry.id === params.get("dataset")) ?? datasets[0]!;

  const from = params.get("from") ?? daysAgo(defaultDays);
  const to = params.get("to") ?? "";
  const stock = params.get("stock_code") ?? "";
  const market = params.get("market") ?? "";
  const day = params.get("standard_date") ?? "";
  // 쪽은 URL에 둔다 — 3쪽을 보다 새로고침해도 3쪽이다.
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const scoped = new URLSearchParams();
  if (active.filters.includes("dates")) {
    scoped.set("from", from);
    if (to) scoped.set("to", to);
  }
  if (active.filters.includes("stock") && stock) scoped.set("stock_code", stock);
  if (active.filters.includes("market") && market) scoped.set("market", market);
  if (active.filters.includes("day") && day) scoped.set("standard_date", day);
  if (offset > 0) scoped.set("offset", String(offset));

  const resource = useJson<never>(active.path(scoped));
  // **이름은 장식이라 본 데이터를 막지 않는다.** 아직 안 왔으면 코드가 그대로 보인다.
  const names = useStockNames();

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    // **필터를 바꾸면 첫 쪽으로 간다.** 안 그러면 결과가 한 쪽뿐인 조건에서 빈 3쪽이 보인다.
    next.delete("offset");
    setParams(next);
  };

  const move = (next_offset: number) => {
    const next = new URLSearchParams(params);
    if (next_offset > 0) next.set("offset", String(next_offset));
    else next.delete("offset");
    setParams(next);
  };

  const pick = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("dataset", id);
    // **데이터셋을 바꾸면 그 데이터셋이 안 쓰는 필터를 지운다.** 남겨 두면 다음에 그
    // 필터를 쓰는 데이터셋으로 갔을 때 보이지 않는 조건이 걸려 있다.
    const wanted = datasets.find((entry) => entry.id === id)?.filters ?? [];
    if (!wanted.includes("stock")) next.delete("stock_code");
    if (!wanted.includes("market")) next.delete("market");
    if (!wanted.includes("day")) next.delete("standard_date");
    next.delete("offset");
    setParams(next);
  };

  const choices = resource.data === null ? null : (active.choices?.(resource.data) ?? null);

  return (
    <section>
      <h2>{title}</h2>
      {note !== undefined && <p className="state">{note}</p>}

      <div className="filters">
        <fieldset>
          <legend>데이터셋</legend>
          {datasets.map((entry) => (
            <label key={entry.id}>
              <input
                type="radio"
                name="dataset"
                checked={entry.id === active.id}
                onChange={() => pick(entry.id)}
              />
              {entry.label}
            </label>
          ))}
        </fieldset>

        {active.filters.includes("dates") && (
          <>
            <label>
              시작일
              <input
                type="date"
                value={from}
                onChange={(event) => set("from", event.target.value)}
              />
            </label>
            <label>
              종료일
              <input type="date" value={to} onChange={(event) => set("to", event.target.value)} />
            </label>
          </>
        )}
        {active.filters.includes("stock") && (
          <label>
            종목코드
            <input
              type="text"
              value={stock}
              placeholder="005930"
              onChange={(event) => set("stock_code", event.target.value)}
            />
          </label>
        )}
        {active.filters.includes("market") && (
          <label>
            시장
            <select value={market} onChange={(event) => set("market", event.target.value)}>
              <option value="">전부</option>
              <option value="KOSPI">KOSPI</option>
              <option value="KOSDAQ">KOSDAQ</option>
            </select>
          </label>
        )}
        {active.filters.includes("day") && (
          <label>
            기준일
            <select value={day} onChange={(event) => set("standard_date", event.target.value)}>
              <option value="">가장 최근</option>
              {(choices?.values ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {active.note !== undefined && <p className="state">{active.note}</p>}

      <Async resource={resource} what={active.label} back="/">
        {(data) => (
          <>
            {active.tables(data, names).map((table) => (
              <DataTable
                key={table.caption}
                caption={table.caption}
                columns={table.columns}
                rows={table.rows as never[]}
                empty={table.empty}
              />
            ))}
            <Pager page={pageOf(data)} onMove={move} />
          </>
        )}
      </Async>
    </section>
  );
}
