// 사건의 기대·실제·판정과 기술적 신호·투자의견.
//
// **판정이 없는 행이 정상이다.** 실제값 주장이 갈리면 판정하지 않고 보류하므로,
// `verdict`가 빈 것을 "아직"으로 읽어야 한다 — 0이나 "없음"으로 채우지 않는다.

import DatasetBrowser, { type Dataset } from "../components/DatasetBrowser";
import type { Column } from "../components/DataTable";
import { integerText, kstText, numberText } from "../format";
import { type StockNames, stockText } from "../stocks";
import type {
  AnalystOpinionRow,
  EventClaimRow,
  EventExtractionRow,
  EventOutcomeRow,
  Items,
  Paged,
  SignalRow,
} from "../types";

function text<T>(key: string, label: string, pick: (row: T) => string | null): Column<T> {
  return { key, label, value: (row) => pick(row) ?? "—" };
}

function num<T>(key: string, label: string, pick: (row: T) => number | null): Column<T> {
  return { key, label, value: (row) => integerText(pick(row)) };
}

function dec<T>(key: string, label: string, pick: (row: T) => number | null, digits = 2): Column<T> {
  return { key, label, value: (row) => numberText(pick(row), digits) };
}

function at<T>(key: string, label: string, pick: (row: T) => string): Column<T> {
  return {
    key,
    label,
    value: (row) => <time dateTime={pick(row)}>{kstText(pick(row))}</time>,
  };
}

const DATASETS: Dataset[] = [
  {
    id: "claims",
    label: "주장",
    note: "문서에서 뽑은 기대·실제. **단위 칸이 없다** — 단위는 지표가 정하고 전부 원이다.",
    filters: ["dates", "stock"],
    path: (params) => `/api/events/claims?${params}`,
    tables: (data: Items<EventClaimRow>, names: StockNames) => [
      {
        caption: "주장 시각 내림차순 · 금액은 원",
        empty: "이 구간에 주장이 없다.",
        rows: data.items,
        columns: [
          { key: "c", label: "종목", value: (row: EventClaimRow) => stockText(row.stock_code, names) },
          text<EventClaimRow>("e", "사건", (row) => row.event_type),
          text<EventClaimRow>("p", "기간", (row) => row.period_key),
          text<EventClaimRow>("m", "지표", (row) => row.metric),
          text<EventClaimRow>("k", "종류", (row) => row.claim_kind),
          num<EventClaimRow>("v", "값(원)", (row) => row.value),
          num<EventClaimRow>("lo", "하단(원)", (row) => row.value_low),
          num<EventClaimRow>("hi", "상단(원)", (row) => row.value_high),
          text<EventClaimRow>("b", "증권사", (row) => row.broker),
          at<EventClaimRow>("at", "주장 시각(KST)", (row) => row.stated_at),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "outcomes",
    label: "판정",
    note: "기대 대비 실제. **첫 성공본 불변이라 다시 내지 않는다.** 판정이 빈 것은 보류다.",
    filters: ["dates", "stock"],
    path: (params) => `/api/events/outcomes?${params}`,
    tables: (data: Items<EventOutcomeRow>, names: StockNames) => [
      {
        caption: "발표 시각 내림차순 · 금액은 원",
        empty: "이 구간에 판정이 없다.",
        rows: data.items,
        columns: [
          { key: "c", label: "종목", value: (row: EventOutcomeRow) => stockText(row.stock_code, names) },
          text<EventOutcomeRow>("e", "사건", (row) => row.event_type),
          text<EventOutcomeRow>("p", "기간", (row) => row.period_key),
          text<EventOutcomeRow>("m", "지표", (row) => row.metric),
          num<EventOutcomeRow>("ex", "기대(원)", (row) => row.expected_value),
          num<EventOutcomeRow>("n", "기대 수", (row) => row.expectation_count),
          num<EventOutcomeRow>("ac", "실제(원)", (row) => row.actual_value),
          dec<EventOutcomeRow>("s", "괴리(%)", (row) => row.surprise_pct),
          {
            key: "v",
            label: "판정",
            value: (row: EventOutcomeRow) =>
              row.verdict === null ? (
                <span className="badge">보류</span>
              ) : (
                <span className={`badge ${row.verdict === "beat" ? "badge-ok" : row.verdict === "miss" ? "badge-bad" : ""}`}>
                  {row.verdict}
                </span>
              ),
          },
          at<EventOutcomeRow>("at", "발표(KST)", (row) => row.announced_at),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "extractions",
    label: "추출 원장",
    note: "**주장 0건 행이 대부분이고 그것이 요점이다** — 뽑았는데 없었던 것과 아직 안 뽑은 것을 가른다.",
    filters: ["dates"],
    path: (params) => `/api/events/extractions?${params}&limit=200`,
    tables: (data: Paged<EventExtractionRow>, _names: StockNames) => [
      {
        caption: "추출 시각 내림차순",
        empty: "이 구간에 추출 이력이 없다.",
        rows: data.items,
        columns: [
          num<EventExtractionRow>("d", "문서 id", (row) => row.document_id),
          num<EventExtractionRow>("n", "주장 수", (row) => row.claim_count),
          text<EventExtractionRow>("m", "모델", (row) => row.llm_model),
          text<EventExtractionRow>("p", "판", (row) => row.prompt_version),
          at<EventExtractionRow>("at", "추출(KST)", (row) => row.extracted_at),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "signals",
    label: "기술적 신호",
    note: "확정 일봉에서 검출한다. **규칙 판이 바뀌면 신호도 갈린다.**",
    filters: ["dates"],
    path: (params) => `/api/events/signals?${params}`,
    tables: (data: Items<SignalRow>, _names: StockNames) => [
      {
        caption: "신호 날짜 내림차순",
        empty: "이 구간에 신호가 없다.",
        rows: data.items,
        columns: [
          text<SignalRow>("s", "심볼", (row) => row.symbol),
          text<SignalRow>("d", "거래일", (row) => row.signal_date),
          text<SignalRow>("k", "종류", (row) => row.kind),
          {
            key: "dir",
            label: "방향",
            value: (row: SignalRow) => (
              <span className={`badge ${row.direction === "up" ? "badge-ok" : "badge-bad"}`}>
                {row.direction}
              </span>
            ),
          },
          dec<SignalRow>("c", "종가", (row) => row.close),
          dec<SignalRow>("s20", "SMA20", (row) => row.sma20),
          dec<SignalRow>("s60", "SMA60", (row) => row.sma60),
          dec<SignalRow>("r", "RSI14", (row) => row.rsi14),
          dec<SignalRow>("vr", "거래량 배수", (row) => row.volume_ratio20),
          text<SignalRow>("rv", "규칙 판", (row) => row.rule_version),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "analyst-opinions",
    label: "투자의견",
    note: "구조화된 숫자는 문서가 아니라 여기 있다. **직전 의견과 달라졌는지가 신호다.**",
    filters: ["dates", "stock"],
    path: (params) => `/api/events/analyst-opinions?${params}`,
    tables: (data: Items<AnalystOpinionRow>, names: StockNames) => [
      {
        caption: "발표일 내림차순 · 금액은 원",
        empty: "이 구간에 투자의견이 없다.",
        rows: data.items,
        columns: [
          { key: "c", label: "종목", value: (row: AnalystOpinionRow) => stockText(row.stock_code, names) },
          text<AnalystOpinionRow>("d", "발표일", (row) => row.business_date),
          text<AnalystOpinionRow>("b", "증권사", (row) => row.broker_name),
          text<AnalystOpinionRow>("o", "의견", (row) => row.opinion),
          text<AnalystOpinionRow>("po", "직전 의견", (row) => row.previous_opinion),
          num<AnalystOpinionRow>("t", "목표주가(원)", (row) => row.target_price),
          num<AnalystOpinionRow>("pc", "전일 종가(원)", (row) => row.previous_close),
          dec<AnalystOpinionRow>("g", "괴리율(%)", (row) => row.gap_rate),
        ] as Column<never>[],
      },
    ],
  },
] as Dataset[];

export default function EventsPage() {
  return (
    <DatasetBrowser
      title="사건·신호"
      note="사건의 기대와 실제, 그 판정, 그리고 기술적 신호와 투자의견."
      datasets={DATASETS}
      defaultDays={90}
    />
  );
}
