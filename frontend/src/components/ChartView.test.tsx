// uPlot lifecycle. `GraphView.test.tsx`와 같은 성격이다 — jsdom에는 canvas가 없고,
// 여기서 볼 것은 그리기가 아니라 **instance를 하나만 만들고 unmount에서 destroy 하는가**다.

import { render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { ChartData } from "../chart";

const instances: { destroy: () => void }[] = [];
const setData = vi.fn();
const setSize = vi.fn();
const created: unknown[] = [];

vi.mock("uplot/dist/uPlot.min.css", () => ({}));
vi.mock("uplot", () => {
  class FakePlot {
    setData = setData;
    setSize = setSize;
    destroy = vi.fn();

    constructor(options: unknown, data: unknown) {
      created.push({ options, data });
      instances.push(this);
    }

    static paths = { bars: () => () => null };
  }
  return { default: FakePlot };
});

const { default: ChartView } = await import("./ChartView");

const DATA: ChartData = [[1, 2, 3], [10, 11, 12], [100, null, 300]];

// jsdom에는 `ResizeObserver`가 없다. uPlot이 스스로 크기를 따라가지 않아 컴포넌트가
// 그것을 쓰므로, 없으면 렌더 자체가 죽는다.
const disconnect = vi.fn();

beforeEach(() => {
  // **여기서 지운다.** Testing Library의 자동 cleanup afterEach가 우리 afterEach보다
  // 나중에 돌아서, 앞 테스트의 unmount가 다음 테스트의 카운트로 새어 든다.
  instances.length = 0;
  created.length = 0;
  setData.mockClear();
  setSize.mockClear();
  disconnect.mockClear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = disconnect;
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it("instance를 하나만 만들고 unmount에서 destroy 한다", () => {
  const view = render(<ChartView data={DATA} series={[{ label: "종가" }]} />);

  expect(instances.length).toBe(1);
  expect(instances[0]?.destroy).not.toHaveBeenCalled();

  view.unmount();

  expect(instances[0]?.destroy).toHaveBeenCalledTimes(1);
});

it("데이터만 바뀌면 setData로 갱신하고 새로 만들지 않는다", () => {
  const view = render(<ChartView data={DATA} series={[{ label: "종가" }]} />);
  view.rerender(<ChartView data={[[1, 2], [10, 11]]} series={[{ label: "종가" }]} />);

  expect(instances.length).toBe(1);
  // 첫 렌더의 effect도 setData를 한 번 부른다. 요점은 instance가 하나라는 것이다.
  expect(setData).toHaveBeenCalled();
});

it("계열 구성이 바뀌면 다시 만든다", () => {
  const view = render(<ChartView data={DATA} series={[{ label: "종가" }]} />);
  view.rerender(
    <ChartView data={DATA} series={[{ label: "종가" }, { label: "거래량", secondary: true }]} />,
  );

  expect(instances.length).toBe(2);
  expect(instances[0]?.destroy).toHaveBeenCalledTimes(1);
});

it("거래량은 오른쪽 축에 붙는다", () => {
  // 지수는 거래량이 0이고 종목은 수백만이다. 한 축에 두면 가격 선이 바닥에 깔린다.
  render(
    <ChartView data={DATA} series={[{ label: "종가" }, { label: "거래량", secondary: true, bars: true }]} />,
  );

  const options = (created[0] as { options: { series: { scale?: string }[] } }).options;
  expect(options.series[1]?.scale).toBe("y");
  expect(options.series[2]?.scale).toBe("y2");
});

it("데이터가 바뀌면 x축 라벨도 따라간다", () => {
  // **회귀 가드.** instance는 계열 구성이 바뀔 때만 다시 만들어진다. 라벨 함수를 만들 때
  // 값으로 붙들면 기간을 바꿔도 x축이 옛 라벨을 계속 그린다.
  const view = render(
    <ChartView data={DATA} series={[{ label: "종가" }]} time={false} xLabel={() => "옛날"} />,
  );
  view.rerender(
    <ChartView data={DATA} series={[{ label: "종가" }]} time={false} xLabel={() => "지금"} />,
  );

  const options = (created[0] as {
    options: { axes: { values?: (u: unknown, splits: number[]) => string[] }[] };
  }).options;
  expect(options.axes[0]?.values?.(null, [0])).toEqual(["지금"]);
});

it("시간축이 아니면 x 라벨을 우리가 만든다", () => {
  render(
    <ChartView
      data={DATA}
      series={[{ label: "US" }]}
      time={false}
      xLabel={(value) => `${value}개월`}
    />,
  );

  const options = (created[0] as { options: { scales: { x: { time: boolean } } } }).options;
  expect(options.scales.x.time).toBe(false);
});

it("unmount에서 ResizeObserver도 함께 끊는다", () => {
  const view = render(<ChartView data={DATA} series={[{ label: "종가" }]} />);
  view.unmount();

  expect(disconnect).toHaveBeenCalledTimes(1);
});
