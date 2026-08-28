// 문서 목록. 공시·실적·출처는 같은 화면의 다른 탭이다.
//
// **점수가 빈 것은 "낮다"가 아니라 "아직 안 봤다"다.** 평가에 실패한 문서는 `assessed_at`이
// null로 남고 다음 정시 실행이 다시 집는다 — 그래서 0으로 채우지 않는다.
//
// 본문은 여기 없다. 상세가 원문과 평가를 함께 준다.

import { Link, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import DirectionMark from "../components/DirectionMark";
import Pager, { pageOf } from "../components/Pager";
import { integerText, kstText, numberText, safeHref } from "../format";
import { stockText, useStockNames } from "../stocks";
import type {
  DisclosureItem,
  DocumentList,
  DocumentSourceItem,
  EarningsFactItem,
  Items,
  Paged,
} from "../types";

const TABS = [
  { id: "documents", label: "문서" },
  { id: "disclosures", label: "공시" },
  { id: "earnings", label: "실적" },
  { id: "sources", label: "출처" },
];

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

type Shape = "documents" | "disclosures" | "earnings" | "sources";

/**
 * 무엇을 그릴지는 **탭이 아니라 응답이 정한다.**
 *
 * 탭을 바꾼 직후 한 프레임 동안 새 탭과 옛 응답이 만난다 — 그때 탭으로 갈라 그리면
 * 공시 행에서 문서의 칸을 읽어 화면이 죽는다(2026-08-27 분봉/일봉에서 같은 버그를 고쳤다).
 * 행이 없으면 무엇이든 "없다"만 그리므로 그때는 탭을 따른다.
 */
function shapeOf(data: unknown, tab: string): Shape {
  const first = (data as Items<Record<string, unknown>>).items?.[0];
  if (first === undefined) return tab as Shape;
  if ("slug" in first) return "sources";
  if ("metric" in first) return "earnings";
  if ("rcept_no" in first) return "disclosures";
  return "documents";
}

export default function DocumentsPage() {
  const [params, setParams] = useSearchParams();
  // 태그가 종목코드로 오므로 이름을 붙인다. **이름은 장식이라 본 데이터를 막지 않는다.**
  const names = useStockNames();
  const tab = TABS.find((entry) => entry.id === params.get("tab"))?.id ?? "documents";
  const from = params.get("from") ?? daysAgo(13);
  const to = params.get("to") ?? "";
  const source = params.get("source") ?? "";
  const minScore = params.get("min_score") ?? "";
  const instrument = params.get("instrument") ?? "";
  const q = params.get("q") ?? "";
  // 쪽은 URL에 둔다 — 새로고침해도 보던 쪽이다.
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const path =
    tab === "sources"
      ? `/api/documents/sources${query({ offset: offset || undefined })}`
      : tab === "earnings"
        ? `/api/documents/earnings${query({ offset: offset || undefined })}`
        : tab === "disclosures"
          ? `/api/documents/disclosures${query({ from, to, limit: 200, offset: offset || undefined })}`
          : `/api/documents${query({
              from,
              to,
              source,
              min_score: minScore,
              instrument,
              q,
              limit: 100,
              offset: offset || undefined,
            })}`;

  const resource = useJson<unknown>(path);

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    // **탭이나 필터를 바꾸면 첫 쪽으로 간다.** 안 그러면 빈 3쪽이 보인다.
    next.delete("offset");
    setParams(next);
  };

  const move = (next_offset: number) => {
    const next = new URLSearchParams(params);
    if (next_offset > 0) next.set("offset", String(next_offset));
    else next.delete("offset");
    setParams(next);
  };

  const dated = tab === "documents" || tab === "disclosures";

  return (
    <section>
      <h2>문서</h2>
      <p className="state">
        수집한 기사·리포트·공시와 그 LLM 평가. **원문과 평가를 한 화면에서 맞춰 봐야** "이 기사가 왜
        근거로 뽑혔나"가 읽힌다.
      </p>

      <div className="filters">
        <fieldset>
          <legend>보기</legend>
          {TABS.map((entry) => (
            <label key={entry.id}>
              <input
                type="radio"
                name="tab"
                checked={entry.id === tab}
                onChange={() => set("tab", entry.id)}
              />
              {entry.label}
            </label>
          ))}
        </fieldset>
        {dated && (
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
        {tab === "documents" && (
          <>
            <label>
              출처
              <input
                type="text"
                value={source}
                placeholder="cnbc"
                onChange={(event) => set("source", event.target.value)}
              />
            </label>
            <label>
              최소 점수
              <input
                type="number"
                min={0}
                max={10}
                value={minScore}
                onChange={(event) => set("min_score", event.target.value)}
              />
            </label>
            <label>
              종목 태그
              <input
                type="text"
                value={instrument}
                placeholder="005930"
                onChange={(event) => set("instrument", event.target.value)}
              />
            </label>
            <label>
              제목·요약 검색
              <input type="text" value={q} onChange={(event) => set("q", event.target.value)} />
            </label>
          </>
        )}
      </div>

      <Async resource={resource} what="문서" back="/documents">
        {(data) => {
          const shape = shapeOf(data, tab);
          if (shape === "sources") {
            const rows = (data as Items<DocumentSourceItem>).items;
            if (rows.length === 0) return <Empty>출처가 없다.</Empty>;
            return (
              <>
                <table>
                  <caption>
                    `enabled`가 카테고리를 통째로 끄는 손잡이다 — 이용조건이 문제가 되면 코드가
                    아니라 이 값을 내린다.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">slug</th>
                      <th scope="col">이름</th>
                      <th scope="col">종류</th>
                      <th scope="col">수집 범위</th>
                      <th scope="col">국가</th>
                      <th scope="col">문서</th>
                      <th scope="col">최근 발행(KST)</th>
                      <th scope="col">수집</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.slug}>
                        <td>{row.slug}</td>
                        <td className="wrap">{row.name}</td>
                        <td>{row.source_kind}</td>
                        <td>{row.collection_mode}</td>
                        <td>{row.country ?? "—"}</td>
                        <td>{integerText(row.documents)}</td>
                        <td>
                          {row.latest_at === null ? (
                            "—"
                          ) : (
                            <time dateTime={row.latest_at}>{kstText(row.latest_at)}</time>
                          )}
                        </td>
                        <td>
                          <span className={`badge ${row.enabled ? "badge-ok" : ""}`}>
                            {row.enabled ? "켜짐" : "꺼짐"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Pager page={pageOf(data)} onMove={move} />
              </>
            );
          }

          if (shape === "earnings") {
            const rows = (data as Items<EarningsFactItem>).items;
            if (rows.length === 0) return <Empty>실적 지표가 없다.</Empty>;
            return (
              <>
                <table>
                  <caption>
                    **이것이 실제값의 원본이다** — 기사 산문에서 다시 뽑지 않는다. 금액은 원.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">종목</th>
                      <th scope="col">기간 끝</th>
                      <th scope="col">지표</th>
                      <th scope="col">범위</th>
                      <th scope="col">기준</th>
                      <th scope="col">당기(원)</th>
                      <th scope="col">전년(원)</th>
                      <th scope="col">공시 원문</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={`${row.rcept_no}:${row.metric}`}>
                        <td>{stockText(row.stock_code, names)}</td>
                        <td>{row.period_end}</td>
                        <td>{row.metric}</td>
                        <td>{row.statement_scope}</td>
                        <td>{row.amount_basis}</td>
                        <td>{integerText(row.current_amount)}</td>
                        <td>{integerText(row.prior_year_amount)}</td>
                        <td>
                          {row.url === null ? (
                            row.rcept_no
                          ) : (
                            <a href={row.url} target="_blank" rel="noopener noreferrer">
                              {row.rcept_no}
                            </a>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Pager page={pageOf(data)} onMove={move} />
              </>
            );
          }

          if (shape === "disclosures") {
            const rows = (data as Paged<DisclosureItem>).items;
            if (rows.length === 0) return <Empty>이 구간에 공시가 없다.</Empty>;
            return (
              <>
                <table>
                  <caption>
                    접수일 내림차순. 접수번호가 자연키이고 **그것이 곧 DART 원문 링크다.**
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">접수일</th>
                      <th scope="col">종목</th>
                      <th scope="col">법인</th>
                      <th scope="col" className="wrap">
                        보고서
                      </th>
                      <th scope="col">제출인</th>
                      <th scope="col">원문</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.rcept_no}>
                        <td>{row.receipt_date}</td>
                        <td>{row.stock_code === null ? "—" : stockText(row.stock_code, names)}</td>
                        <td className="wrap">{row.company_name}</td>
                        <td className="wrap">{row.report_name}</td>
                        <td>{row.filer_name ?? "—"}</td>
                        <td>
                          {row.url === null ? (
                            row.rcept_no
                          ) : (
                            <a href={row.url} target="_blank" rel="noopener noreferrer">
                              {row.rcept_no}
                            </a>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Pager page={pageOf(data)} onMove={move} />
              </>
            );
          }

          const list = data as DocumentList;
          if (list.items.length === 0) return <Empty>이 조건에 문서가 없다.</Empty>;
          return (
            <>
              <table>
                <caption>
                  발행 시각 내림차순. **점수가 `—`면 아직 평가하지 않은 문서이고 0이 아니다.**
                </caption>
                <thead>
                  <tr>
                    <th scope="col">발행(KST)</th>
                    <th scope="col">출처</th>
                    <th scope="col" className="wrap">
                      제목
                    </th>
                    <th scope="col">점수</th>
                    <th scope="col">방향</th>
                    <th scope="col">종목 태그</th>
                    <th scope="col">지표 태그</th>
                    <th scope="col">원문</th>
                  </tr>
                </thead>
                <tbody>
                  {list.items.map((row) => {
                    const href = safeHref(row.canonical_url);
                    return (
                      <tr key={row.id}>
                        <td>
                          <time dateTime={row.published_at}>{kstText(row.published_at)}</time>
                        </td>
                        <td>{row.source_slug}</td>
                        <td className="wrap">
                          <Link to={`/documents/${row.id}`}>{row.title}</Link>
                        </td>
                        <td>{numberText(row.value_score, 0)}</td>
                        <td>
                          <DirectionMark value={row.direction} />
                        </td>
                        <td className="wrap">
                          {row.instruments.map((code) => stockText(code, names)).join(" · ") || "—"}
                        </td>
                        <td className="wrap">{row.indicators.join(" · ") || "—"}</td>
                        <td>
                          {href === null ? (
                            "—"
                          ) : (
                            <a href={href} target="_blank" rel="noopener noreferrer">
                              열기
                            </a>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <Pager page={pageOf(data)} onMove={move} />
            </>
          );
        }}
      </Async>
    </section>
  );
}
