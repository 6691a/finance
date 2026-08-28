// API 응답을 uPlot 입력으로. **순수 함수다** — uPlot을 import하지 않고 입력을 바꾸지도 않는다.
//
// ## 왜 x축이 시각이 아니라 **순번**인가
//
// 시장은 하루 종일 열리지 않는다. 코스피는 09:00~15:30이고 나머지 17시간 반은 봉이
// 아예 없다. 주말이면 63시간이 빈다. x축을 실제 시각으로 두면 그 빈 시간이 차트 폭의
// 대부분을 먹고, 거래가 실제로 일어난 구간이 좌우로 짓눌린다.
//
// 그래서 x는 **0, 1, 2… 순번**이고 각 순번의 표시 이름을 `labels`가 따로 갖는다.
// 봉이 연달아 붙으므로 장 마감과 다음 개장 사이의 빈 구간이 사라진다. 금융 차트가
// 보통 이렇게 그린다.
//
// **대신 잃는 것이 있다:** 순번 축에서는 한 시간의 공백과 사흘의 공백이 똑같이 한 칸이라
// 눈으로 구분되지 않는다. 그래서 축 라벨에 날짜를 함께 적는다 — 라벨이 `08/27`에서
// `08/31`로 뛰면 그 사이가 주말이다.
//
// **빈 값을 0으로 채우지 않는다.** uPlot은 `null`을 선의 끊김으로 그린다 — 0으로 채우면
// 바닥으로 떨어지는 거짓 급락이 된다. 없는 봉은 애초에 배열에 없다(순번이 건너뛰지 않는다).

import { kstAxisText } from "./format";
import type { BarSeries, DailySeries, IndicatorPoints } from "./types";

/** uPlot이 먹는 모양. 첫 배열이 x, 나머지가 계열이다. */
export type ChartData = [number[], ...(number | null)[][]];

/** 순번 축 차트 하나. `labels[i]`가 `data[0][i]`의 표시 이름이다. */
export interface OrdinalChart {
  data: ChartData;
  labels: string[];
}

/** 0부터 `count - 1`까지의 순번. x축이 이것이다. */
export function ordinal(count: number): number[] {
  return Array.from({ length: count }, (_, index) => index);
}

/**
 * 순번 → 라벨. uPlot의 눈금 값은 정수가 아닐 수 있어(축이 숫자축이다) 반올림해서 찾고,
 * 범위 밖이면 빈 문자열이라 눈금이 조용히 사라진다.
 */
export function labelAt(labels: readonly string[], value: number): string {
  return labels[Math.round(value)] ?? "";
}

/**
 * 분봉 → 종가와 거래량 둘.
 *
 * 거래량을 같은 차트에 두되 **축을 나눈다**(`ChartView`가 y2를 준다). 지수는 거래량이
 * 0이고 종목은 수백만이라 한 축에 두면 가격 선이 바닥에 깔린다.
 */
export function barChartData(series: BarSeries): OrdinalChart {
  return {
    data: [
      ordinal(series.times.length),
      [...series.close],
      series.volume.map((value) => (value === null || value === 0 ? null : value)),
    ],
    labels: series.times.map(kstAxisText),
  };
}

export function dailyChartData(series: DailySeries): OrdinalChart {
  return {
    data: [
      ordinal(series.dates.length),
      [...series.close],
      series.volume.map((value) => (value === null || value === 0 ? null : value)),
    ],
    // 일봉은 하루가 한 칸이라 시각을 적을 것이 없다.
    labels: [...series.dates],
  };
}

export function indicatorChartData(series: IndicatorPoints): OrdinalChart {
  return {
    data: [ordinal(series.dates.length), [...series.values]],
    labels: [...series.dates],
  };
}

/**
 * 곡선 차트. **x축이 시간도 순번도 아니라 만기(개월)다.**
 *
 * 여기서는 간격 자체가 뜻을 갖는다 — 2년과 10년 사이가 10년과 30년 사이보다 좁아야
 * 곡선의 기울기가 사실대로 보인다. 그래서 순번으로 바꾸지 않는다.
 *
 * 나라마다 고시하는 만기가 다르므로 x축은 **나온 만기 전부의 합집합**이고, 그 나라가
 * 고시하지 않는 만기는 `null`이라 선이 그 점을 건너뛴다.
 */
export function curveChartData(
  countries: { country: string; points: { maturity_months: number; value: number }[] }[],
): { data: ChartData; labels: string[] } {
  const maturities = [
    ...new Set(countries.flatMap((entry) => entry.points.map((point) => point.maturity_months))),
  ].sort((a, b) => a - b);

  return {
    data: [
      maturities,
      ...countries.map((entry) => {
        const byMaturity = new Map(entry.points.map((point) => [point.maturity_months, point.value]));
        return maturities.map((maturity) => byMaturity.get(maturity) ?? null);
      }),
    ] as ChartData,
    labels: countries.map((entry) => entry.country),
  };
}

/** 만기를 사람이 읽는 이름으로. 곡선의 x축 라벨이다. */
export function maturityText(months: number): string {
  if (months < 12) return `${months}개월`;
  return months % 12 === 0 ? `${months / 12}년` : `${(months / 12).toFixed(1)}년`;
}

/** 마지막 값과 그 시각. 화면 상단에 적는다 — 차트만으로는 정확한 값을 못 읽는다. */
export function lastPoint(times: readonly string[], values: readonly number[]): {
  at: string;
  value: number;
} | null {
  if (times.length === 0 || values.length === 0) return null;
  const at = times[times.length - 1];
  const value = values[values.length - 1];
  return at === undefined || value === undefined ? null : { at, value };
}
