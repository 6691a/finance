// 수집 감시와 마스터.
//
// **이 화면의 질문은 "무엇이 안 들어오고 있나"다.** 그래서 요약이 첫 데이터셋이고 늦은
// 출처가 맨 위다. 2만 행을 훑어 이상을 찾는 화면이 아니다.
//
// `running`을 성공·실패와 따로 센다 — 종료를 기록하지 못한 수집이 몇 건인지가 "끊긴 것이
// 있나"의 첫 신호이고, 묻어 두면 그 신호가 사라진다.

import DatasetBrowser, { type Dataset } from "../components/DatasetBrowser";
import type { Column } from "../components/DataTable";
import { integerText, kstText } from "../format";
import type {
  InstrumentRow,
  Items,
  MarketSessionRow,
  Paged,
  SourceHealth,
  SourceHealthList,
  SourceRecordRow,
} from "../types";

function text<T>(key: string, label: string, pick: (row: T) => string | null): Column<T> {
  return { key, label, value: (row) => pick(row) ?? "—" };
}

function num<T>(key: string, label: string, pick: (row: T) => number | null): Column<T> {
  return { key, label, value: (row) => integerText(pick(row)) };
}

function at<T>(key: string, label: string, pick: (row: T) => string | null): Column<T> {
  return {
    key,
    label,
    value: (row) => {
      const value = pick(row);
      return value === null ? "—" : <time dateTime={value}>{kstText(value)}</time>;
    },
  };
}

/** 참·거짓·모름 셋을 글자로. `null`은 "안 밝혔다"이지 거짓이 아니다. */
function flag<T>(key: string, label: string, pick: (row: T) => boolean | null): Column<T> {
  return {
    key,
    label,
    value: (row) => {
      const value = pick(row);
      return value === null ? "—" : value ? "예" : "아니오";
    },
  };
}

const DATASETS: Dataset[] = [
  {
    id: "health",
    label: "출처별 최신성",
    note: "**늦은 것이 위다.** 최근 24시간을 본다 — 매일 도는 수집이 한 번은 들어 있다.",
    filters: [],
    path: () => "/api/collection/health",
    tables: (data: SourceHealthList) => [
      {
        caption: `기준 ${kstText(data.since)} 이후`,
        empty: "이 창에 수집 기록이 없다.",
        rows: data.items,
        columns: [
          text<SourceHealth>("s", "출처", (row) => row.source),
          text<SourceHealth>("t", "방식", (row) => row.source_type),
          at<SourceHealth>("at", "마지막 수집(KST)", (row) => row.latest_at),
          num<SourceHealth>("n", "수집", (row) => row.records),
          num<SourceHealth>("ok", "성공", (row) => row.succeeded),
          {
            key: "bad",
            label: "실패",
            value: (row: SourceHealth) =>
              row.failed === 0 ? "0" : <span className="badge badge-bad">{row.failed}</span>,
          },
          {
            key: "run",
            label: "종료 미기록",
            value: (row: SourceHealth) =>
              row.running === 0 ? "0" : <span className="badge">{row.running}</span>,
          },
          num<SourceHealth>("q", "격리", (row) => row.quarantined),
          num<SourceHealth>("rows", "만든 행", (row) => row.rows),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "records",
    label: "수집 레코드",
    note: "**`payload`는 없다** — 있는지 여부만 낸다. jsonb 원본이라 행 하나가 수백 KB일 수 있다.",
    filters: ["dates"],
    path: (params) => `/api/collection/records?${params}&limit=200`,
    tables: (data: Paged<SourceRecordRow>) => [
      {
        caption: "시작 시각 내림차순",
        empty: "이 구간에 수집 레코드가 없다.",
        rows: data.items,
        columns: [
          num<SourceRecordRow>("id", "id", (row) => row.id),
          text<SourceRecordRow>("s", "출처", (row) => row.source),
          // `source_key`는 종목코드일 때도 파일 이름·시계열 id일 때도 있다. 종목만
          // 골라 이름을 붙이면 나머지가 무엇인지 되레 흐려져서 원본을 그대로 둔다.
          text<SourceRecordRow>("k", "무엇을", (row) => row.source_key),
          at<SourceRecordRow>("st", "시작(KST)", (row) => row.started_at),
          at<SourceRecordRow>("en", "끝(KST)", (row) => row.completed_at),
          {
            key: "status",
            label: "상태",
            value: (row: SourceRecordRow) => (
              <span
                className={`badge ${row.status === "succeeded" ? "badge-ok" : row.status === "failed" ? "badge-bad" : ""}`}
              >
                {row.status}
              </span>
            ),
          },
          num<SourceRecordRow>("rc", "만든 행", (row) => row.record_count),
          flag<SourceRecordRow>("p", "원본 보관", (row) => row.has_payload),
          flag<SourceRecordRow>("m", "메타", (row) => row.has_metadata),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "instruments",
    label: "추적 종목",
    note: "우리가 이름을 아는 종목. **행이 있다는 것과 시세를 받는 것은 다르다** — 행이 있으면 문서에서 그 종목을 알아보고 리서치를 받고, `시세 수집`이 참이어야 봉까지 받는다(2026-08-31 기준 20종목 중 2종목).",
    filters: [],
    path: () => "/api/collection/instruments",
    tables: (data: Items<InstrumentRow>) => [
      {
        caption: "시장·티커 순",
        empty: "마스터가 비어 있다.",
        rows: data.items,
        columns: [
          text<InstrumentRow>("t", "티커", (row) => row.ticker),
          text<InstrumentRow>("m", "시장", (row) => row.market),
          text<InstrumentRow>("n", "이름", (row) => row.name),
          text<InstrumentRow>("k", "종류", (row) => row.kind),
          text<InstrumentRow>("c", "통화", (row) => row.currency),
          text<InstrumentRow>("s", "소스 심볼", (row) => row.source_symbol),
          flag<InstrumentRow>("w", "시세 수집", (row) => row.is_watched),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "sessions",
    label: "개장 캘린더",
    note: "오늘 앞뒤 30일. **검증이 붙으면 제공처 값과 우리 판정이 갈릴 수 있다.**",
    filters: [],
    path: () => "/api/collection/sessions",
    tables: (data: Items<MarketSessionRow>) => [
      {
        caption: "날짜 오름차순",
        empty: "이 구간에 캘린더가 없다.",
        rows: data.items,
        columns: [
          text<MarketSessionRow>("d", "날짜", (row) => row.session_date),
          text<MarketSessionRow>("m", "시장", (row) => row.market_code),
          text<MarketSessionRow>("n", "이름", (row) => row.market_name),
          flag<MarketSessionRow>("b", "영업일", (row) => row.kis_business_day),
          flag<MarketSessionRow>("t", "거래일", (row) => row.kis_trading_day),
          flag<MarketSessionRow>("o", "개장(제공처)", (row) => row.kis_open_day),
          flag<MarketSessionRow>("e", "개장(우리 판정)", (row) => row.effective_open_day),
          text<MarketSessionRow>("ls", "현지 결제일", (row) => row.local_settlement_date),
          text<MarketSessionRow>("v", "확인 방법", (row) => row.verified_by),
          // **null이면 제공처 값을 그대로 믿고 있다.** 확인한 적 없음과 확인해서 같음은 다르다.
          at<MarketSessionRow>("va", "확인 시각(KST)", (row) => row.verified_at),
        ] as Column<never>[],
      },
    ],
  },
] as Dataset[];

export default function CollectionPage() {
  return (
    <DatasetBrowser
      title="수집"
      note="무엇이 언제까지 채워졌나. 수집을 다시 돌리는 손잡이는 여기가 아니라 Airflow UI다."
      datasets={DATASETS}
      defaultDays={2}
    />
  );
}
