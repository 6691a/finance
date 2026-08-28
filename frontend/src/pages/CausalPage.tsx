// 주간 사후 인과 그래프. 사건 → 채널 체인 → 대상.
//
// **이 화면은 인과의 증명이 아니다.** `confidence`가 `observed`(같은 기간에 함께 관찰됨)와
// `plausible`(해석) 둘뿐이고 둘 다 "그래서 그렇게 됐다"가 아니다. 화면이 그 말을 먼저 한다 —
// 표만 보면 화살표가 인과로 읽힌다.
//
// **실현 등락에는 언제나 단위가 붙는다.** `KTB10Y`의 +7.4bp와 KOSPI의 +10.77%가 한 칸에
// 들어가면 크기 비교가 조용히 무의미해진다.

import { Link } from "react-router-dom";

import DatasetBrowser, { type Dataset } from "../components/DatasetBrowser";
import type { Column } from "../components/DataTable";
import { integerText, numberText } from "../format";
import { CAUSAL_CONFIDENCES, CAUSAL_SIGNS, CAUSAL_TARGET_KINDS, labelOf } from "../labels";
import type { CausalChannelRow, CausalEventRow, CausalPathRow, Items } from "../types";

/** 실현 등락 한 칸. 단위가 퍼센트면 `%`, 금리면 `bp`다. */
export function changeText(value: number, unit: string): string {
  const suffix = unit === "basis_point" ? "bp" : "%";
  const digits = unit === "basis_point" ? 1 : 2;
  return `${numberText(value, digits)}${suffix}`;
}

/** 체인 한 줄. 사건에서 대상까지의 순서를 그대로 보인다. */
export function chainText(row: CausalPathRow): string {
  return [row.event_title, ...row.channels, row.target_code].join(" → ");
}

function change<T extends { return_unit: string }>(
  key: string,
  label: string,
  pick: (row: T) => number,
): Column<T> {
  return { key, label, value: (row) => changeText(pick(row), row.return_unit) };
}

const DATASETS: Dataset[] = [
  {
    id: "paths",
    label: "경로",
    note: "**인과의 증명이 아니다** — `함께 관찰`은 같은 기간에 함께 움직였다는 뜻이고 `해석`은 모델의 말이다.",
    filters: ["dates"],
    path: (params) => `/api/causal/paths?${params}`,
    tables: (data: Items<CausalPathRow>) => [
      {
        caption: "주 내림차순 · 체인은 사건에서 대상 순서 · 등락 단위는 행마다 다르다(%·bp)",
        empty: "이 구간에 경로가 없다.",
        rows: data.items,
        columns: [
          { key: "w", label: "주", value: (row: CausalPathRow) => row.week_start },
          {
            key: "chain",
            label: "체인",
            // 누르면 그 사건의 그래프로 간다. 체인이 이 표에서 가장 긴 칸이라 과녁이 넓다.
            value: (row: CausalPathRow) => <Link to={`/causal/${row.id}`}>{chainText(row)}</Link>,
          },
          {
            key: "tk",
            label: "대상 종류",
            value: (row: CausalPathRow) => labelOf(CAUSAL_TARGET_KINDS, row.target_kind),
          },
          {
            key: "s",
            label: "방향",
            value: (row: CausalPathRow) => (
              <span className={row.sign === "up" ? "up" : "down"}>
                {labelOf(CAUSAL_SIGNS, row.sign)}
              </span>
            ),
          },
          {
            key: "c",
            label: "근거의 성격",
            value: (row: CausalPathRow) => labelOf(CAUSAL_CONFIDENCES, row.confidence),
          },
          change<CausalPathRow>("rw", "그 주", (row) => row.return_week_change),
          change<CausalPathRow>("r1", "T+1", (row) => row.return_t1_change),
          change<CausalPathRow>("r5", "T+5", (row) => row.return_t5_change),
          { key: "why", label: "설명", value: (row: CausalPathRow) => row.reasoning },
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "events",
    label: "사건",
    note: "그 주에 실제로 일어난 일. **경로가 0인 사건도 남는다** — 사건은 있는데 경로가 안 나온 것도 사실이다.",
    filters: ["dates"],
    path: (params) => `/api/causal/events?${params}`,
    tables: (data: Items<CausalEventRow>) => [
      {
        caption: "발생일 내림차순",
        empty: "이 구간에 사건이 없다.",
        rows: data.items,
        columns: [
          { key: "t", label: "사건", value: (row: CausalEventRow) => row.title },
          { key: "d", label: "발생일", value: (row: CausalEventRow) => row.occurred_on },
          {
            key: "w",
            label: "처음 등장한 주",
            value: (row: CausalEventRow) => row.first_seen_week,
          },
          { key: "p", label: "경로", value: (row: CausalEventRow) => integerText(row.paths) },
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "channels",
    label: "채널",
    note: "전달 경로 이름. 주가 쌓이면서 같은 채널을 공유해 다중 홉이 생긴다.",
    filters: ["dates"],
    path: (params) => `/api/causal/channels?${params}`,
    tables: (data: Items<CausalChannelRow>) => [
      {
        caption: "처음 등장한 주 내림차순",
        empty: "이 구간에 채널이 없다.",
        rows: data.items,
        columns: [
          { key: "n", label: "채널", value: (row: CausalChannelRow) => row.name },
          {
            key: "w",
            label: "처음 등장한 주",
            value: (row: CausalChannelRow) => row.first_seen_week,
          },
          {
            key: "s",
            label: "거친 단계",
            value: (row: CausalChannelRow) => integerText(row.steps),
          },
        ] as Column<never>[],
      },
    ],
  },
];

export default function CausalPage() {
  return (
    <DatasetBrowser
      title="인과 그래프"
      note="한 주에 일어난 일이 어떤 경로로 어떤 대상에 닿았는지를 LLM이 사후에 정리한 것이다. **인과의 증명이 아니다.**"
      datasets={DATASETS}
      defaultDays={84}
    />
  );
}
