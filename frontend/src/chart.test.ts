import { describe, expect, it } from "vitest";

import {
  barChartData,
  curveChartData,
  dailyChartData,
  indicatorChartData,
  labelAt,
  lastPoint,
  maturityText,
  ordinal,
} from "./chart";
import type { BarSeries, DailySeries, IndicatorPoints } from "./types";

const BARS: BarSeries = {
  kind: "index",
  symbol: "KOSPI",
  exchange: null,
  provider: "kis",
  interval: "5m",
  points: 3,
  times: ["2026-08-27T00:00:00Z", "2026-08-27T00:05:00Z", "2026-08-27T00:10:00Z"],
  open: [3200, 3205, 3210],
  high: [3206, 3212, 3215],
  low: [3199, 3203, 3208],
  close: [3205, 3210, 3212],
  volume: [1000, 0, null],
};

describe("barChartData", () => {
  it("입력을 바꾸지 않는다", () => {
    const before = JSON.stringify(BARS);
    barChartData(BARS);
    expect(JSON.stringify(BARS)).toBe(before);
  });

  it("x와 계열 둘을 같은 길이로 낸다", () => {
    const { data } = barChartData(BARS);
    const [x, close, volume] = data;
    expect(x.length).toBe(3);
    expect(close?.length).toBe(3);
    expect(volume?.length).toBe(3);
  });

  it("x축이 시각이 아니라 순번이다", () => {
    // 시각 축이면 장 마감과 다음 개장 사이 17시간 반이 차트 폭의 대부분을 먹는다.
    const { data } = barChartData(BARS);
    expect(data[0]).toEqual([0, 1, 2]);
  });

  it("**장 마감과 다음 개장이 한 칸 옆으로 붙는다**", () => {
    // 코스피 15:30 마감(06:30Z) 다음 봉이 이튿날 09:00(00:00Z)이다. 그 사이 17시간 반에
    // 봉이 없고, 순번 축에서는 그것이 빈 구간이 아니라 그냥 다음 칸이다.
    const overnight = {
      ...BARS,
      points: 2,
      times: ["2026-08-27T06:30:00Z", "2026-08-28T00:00:00Z"],
      open: [3200, 3210],
      high: [3206, 3215],
      low: [3199, 3208],
      close: [3205, 3212],
      volume: [1000, 2000],
    };

    const { data, labels } = barChartData(overnight);
    expect(data[0]).toEqual([0, 1]);
    // 라벨이 날짜를 함께 적어 그 한 칸이 하루를 넘었다는 것이 읽힌다.
    expect(labels[0]).toBe("08/27 15:30");
    expect(labels[1]).toBe("08/28 09:00");
  });

  it("라벨은 KST 짧은 표기다", () => {
    const { labels } = barChartData(BARS);
    expect(labels).toEqual(["08/27 09:00", "08/27 09:05", "08/27 09:10"]);
  });

  it("거래량 0과 null을 둘 다 null로 둔다", () => {
    // 지수는 거래량 개념이 없어 제공처가 0을 실어 보낸다. 0으로 그리면 막대가 바닥에 깔린다.
    const { data } = barChartData(BARS);
    expect(data[2]).toEqual([1000, null, null]);
  });

  it("종가는 0으로 채우지 않는다", () => {
    const { data } = barChartData(BARS);
    expect(data[1]).toEqual([3205, 3210, 3212]);
  });
});

describe("ordinal · labelAt", () => {
  it("순번은 0부터 센다", () => {
    expect(ordinal(3)).toEqual([0, 1, 2]);
    expect(ordinal(0)).toEqual([]);
  });

  it("눈금 값이 정수가 아니어도 라벨을 찾는다", () => {
    // uPlot의 축은 숫자축이라 splits가 0.5 같은 값으로 온다.
    expect(labelAt(["a", "b", "c"], 1.4)).toBe("b");
    expect(labelAt(["a", "b", "c"], 1.6)).toBe("c");
  });

  it("범위 밖은 빈 문자열이라 눈금이 조용히 사라진다", () => {
    expect(labelAt(["a"], 5)).toBe("");
    expect(labelAt(["a"], -1)).toBe("");
  });
});

describe("dailyChartData", () => {
  it("거래일 축을 쓴다", () => {
    const daily: DailySeries = {
      kind: "index",
      symbol: "KOSPI",
      exchange: null,
      provider: "yahoo",
      points: 2,
      dates: ["2026-08-25", "2026-08-26"],
      open: [1, 2],
      high: [1, 2],
      low: [1, 2],
      close: [1.5, 2.5],
      volume: [10, null],
    };

    const { data, labels } = dailyChartData(daily);
    expect(data[0]).toEqual([0, 1]);
    expect(labels).toEqual(["2026-08-25", "2026-08-26"]);
    expect(data[1]).toEqual([1.5, 2.5]);
  });

  it("휴장일을 건너뛴 두 거래일이 붙는다", () => {
    // 금요일 다음이 월요일이다. 주말 63시간을 차트가 그리지 않는다.
    const weekend: DailySeries = {
      kind: "index",
      symbol: "KOSPI",
      exchange: null,
      provider: "yahoo",
      points: 2,
      dates: ["2026-08-28", "2026-08-31"],
      open: [1, 2],
      high: [1, 2],
      low: [1, 2],
      close: [1.5, 2.5],
      volume: [10, 20],
    };

    expect(dailyChartData(weekend).data[0]).toEqual([0, 1]);
  });
});

describe("indicatorChartData", () => {
  it("계열 하나만 낸다", () => {
    const points: IndicatorPoints = {
      provider: "fred",
      series_id: "DGS10",
      kind: "government_bond",
      label: "미국 10년물",
      unit: "Percent",
      points: 2,
      dates: ["2026-08-24", "2026-08-25"],
      values: [4.7, 4.72],
    };

    const { data, labels } = indicatorChartData(points);
    expect(data.length).toBe(2);
    expect(data[0]).toEqual([0, 1]);
    expect(data[1]).toEqual([4.7, 4.72]);
    expect(labels).toEqual(["2026-08-24", "2026-08-25"]);
  });
});

describe("curveChartData", () => {
  // **곡선만 순번이 아니다.** 만기 간격 자체가 뜻을 가져서, 2년과 10년 사이가 10년과
  // 30년 사이보다 좁아야 기울기가 사실대로 보인다.
  const countries = [
    { country: "US", points: [{ maturity_months: 24, value: 4.1 }, { maturity_months: 120, value: 4.6 }] },
    { country: "JP", points: [{ maturity_months: 120, value: 2.9 }, { maturity_months: 240, value: 3.7 }] },
  ];

  it("만기의 합집합을 x축으로 쓴다", () => {
    const { data } = curveChartData(countries);
    expect(data[0]).toEqual([24, 120, 240]);
  });

  it("고시하지 않는 만기는 null이라 선이 건너뛴다", () => {
    // 0으로 채우면 곡선이 바닥으로 꺾이고 "그 만기 금리가 0"으로 읽힌다.
    const { data, labels } = curveChartData(countries);
    expect(labels).toEqual(["US", "JP"]);
    expect(data[1]).toEqual([4.1, 4.6, null]);
    expect(data[2]).toEqual([null, 2.9, 3.7]);
  });
});

describe("maturityText", () => {
  it("1년 미만은 개월로 적는다", () => {
    expect(maturityText(3)).toBe("3개월");
    expect(maturityText(6)).toBe("6개월");
  });

  it("연 단위로 떨어지면 년으로 적는다", () => {
    expect(maturityText(120)).toBe("10년");
    expect(maturityText(360)).toBe("30년");
  });
});

describe("lastPoint", () => {
  it("빈 배열이면 null이다", () => {
    expect(lastPoint([], [])).toBeNull();
  });

  it("마지막 값과 그 시각을 함께 준다", () => {
    expect(lastPoint(BARS.times, BARS.close)).toEqual({
      at: "2026-08-27T00:10:00Z",
      value: 3212,
    });
  });
});
