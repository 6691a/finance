// uPlot lifecycle만 맡는다. `GraphView`와 같은 계약이다 — **instance는 하나이고**
// 데이터가 바뀌면 `setData()`로 갱신하며 unmount에서 `destroy()`한다.
//
// **`react-uplot` 같은 래퍼를 쓰지 않는다.** uPlot은 framework-agnostic이라 `ref`로
// container를 받고 `useEffect`에서 만들면 끝이다. Cytoscape를 감싸지 않은 것과 같은 이유다.
//
// uPlot은 스스로 크기를 따라가지 않는다. `ResizeObserver`가 그 일을 하고 unmount에서
// 함께 끊는다.

import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import type { ChartData } from "../chart";

// **stylesheet의 토큰을 JS가 못 읽는다.** uPlot 설정은 CSS가 아니라 객체라 `var(--ink)`가
// 안 통한다. 값이 여기 한 벌 더 있고, 화면 팔레트를 바꾸면 여기도 함께 바꾼다.
const MUTED = "#98a2b0";
const GRID = "#2a2f39";

// 계열 색. 다섯을 넘으면 순환한다 — 곡선이 나라 아홉까지 가므로 모양(점)으로도 가른다.
export const SERIES_COLORS = ["#6ea8fe", "#4ade80", "#f87171", "#d9a441", "#c084fc", "#22d3ee"];

export interface ChartSeries {
  label: string;
  /** 오른쪽 축에 붙일 계열. 거래량이 그렇다 — 가격과 자릿수가 달라 한 축에 못 둔다. */
  secondary?: boolean;
  /** 선이 아니라 막대로. 거래량용이다. */
  bars?: boolean;
}

export interface ChartViewProps {
  data: ChartData;
  series: ChartSeries[];
  /** x축이 시간인가. 곡선은 만기(개월)라 false다. */
  time?: boolean;
  /** x축 값을 사람이 읽는 라벨로. 시간축이 아닐 때만 쓴다. */
  xLabel?: (value: number) => string;
  height?: number;
}

function stroke(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length] as string;
}

export default function ChartView({
  data,
  series,
  time = true,
  xLabel,
  height = 340,
}: ChartViewProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const instance = useRef<uPlot | null>(null);
  // 최신 설정을 참조로 들고 있어 데이터가 바뀔 때마다 instance를 다시 만들지 않는다.
  const shape = useRef({ series, time, xLabel, height });
  shape.current = { series, time, xLabel, height };

  useEffect(() => {
    const element = container.current;
    if (element === null) return;

    const { series: shapes, time: isTime, height: box } = shape.current;
    // **라벨은 스냅샷이 아니라 ref에서 읽는다.** instance는 계열 구성이 바뀔 때만 다시
    // 만들어지므로, 여기서 `xLabel`을 값으로 붙들면 기간·축을 바꿔도 x축이 옛 데이터의
    // 라벨을 계속 그린다.
    const labelOf = (value: number) => shape.current.xLabel?.(value) ?? String(value);
    const options: uPlot.Options = {
      width: element.clientWidth || 600,
      height: box,
      // 시간축이면 uPlot이 epoch 초를 현지 시간대로 그린다. 프로젝트가 KST 표시라 그대로 둔다.
      ...(isTime ? {} : { mode: 1 as const }),
      scales: {
        x: { time: isTime },
        y: { auto: true },
        y2: { auto: true },
      },
      axes: [
        {
          stroke: MUTED,
          grid: { stroke: GRID, width: 1 },
          ticks: { stroke: GRID },
          ...(isTime ? {} : { values: (_: uPlot, splits: number[]) => splits.map(labelOf) }),
        },
        { stroke: MUTED, grid: { stroke: GRID, width: 1 }, ticks: { stroke: GRID } },
        { stroke: MUTED, side: 1, scale: "y2", grid: { show: false }, ticks: { stroke: GRID } },
      ],
      // `exactOptionalPropertyTypes`가 켜져 있어 `undefined`를 명시로 넣을 수 없다.
      // 없는 칸은 아예 안 넣는다 — 스프레드로 조건부로 붙인다.
      series: [
        {
          label: isTime ? "시각" : "x",
          ...(isTime ? {} : { value: (_: uPlot, value: number) => labelOf(value) }),
        },
        ...shapes.map((entry, index): uPlot.Series => {
          const bars = uPlot.paths.bars?.({ size: [0.6, 24] });
          return {
            label: entry.label,
            stroke: stroke(index),
            width: entry.bars ? 0 : 1.6,
            scale: entry.secondary ? "y2" : "y",
            points: { show: false },
            ...(entry.bars ? { fill: `${stroke(index)}55` } : {}),
            ...(entry.bars && bars ? { paths: bars } : {}),
          };
        }),
      ],
      legend: { show: true },
      cursor: { drag: { x: true, y: false } },
    };

    const plot = new uPlot(options, data as unknown as uPlot.AlignedData, element);
    instance.current = plot;

    const observer = new ResizeObserver(() => {
      plot.setSize({ width: element.clientWidth, height: shape.current.height });
    });
    observer.observe(element);

    return () => {
      observer.disconnect();
      plot.destroy();
      instance.current = null;
    };
    // 계열 **구성**이 바뀔 때만 다시 만든다. 데이터만 바뀌면 아래 effect가 갱신한다.
  }, [series.length, time]);

  useEffect(() => {
    instance.current?.setData(data as unknown as uPlot.AlignedData);
  }, [data]);

  // uPlot이 여기에 canvas를 붙인다. 색은 `styles.css`의 `.uplot` 블록이 마무리한다.
  return <div className="chart-canvas" ref={container} data-testid="chart-canvas" />;
}
