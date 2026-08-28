// 지표 화면과 곡선.
//
// 주제 둘: ① `kind`가 다른 계열이 한 축에 섞이지 않는다 ② 곡선이 만기 순이고 만기 없는
// 계열은 애초에 들어오지 않는다.

import { screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { CurveResponse, IndicatorPoints, IndicatorSeriesList } from "../types";
import { renderAt, stubFetch } from "../test-harness";

vi.mock("uplot/dist/uPlot.min.css", () => ({}));
vi.mock("uplot", () => {
  class FakePlot {
    setData = vi.fn();
    setSize = vi.fn();
    destroy = vi.fn();
    static paths = { bars: () => () => null };
  }
  return { default: FakePlot };
});

const { default: IndicatorsPage, maturityLabel } = await import("./IndicatorsPage");
const { default: IndicatorDetailPage } = await import("./IndicatorDetailPage");
const { default: CurvePage } = await import("./CurvePage");

const SERIES: IndicatorSeriesList = {
  items: [
    {
      provider: "fred",
      series_id: "DGS10",
      kind: "government_bond",
      country: "US",
      country_name: "미국",
      label: "미국 10년물",
      maturity_months: 120,
      unit: "Percent",
      rows: 78,
      observed_from: "2026-02-01",
      observed_to: "2026-08-25",
    },
    {
      provider: "fred",
      series_id: "CPI_M",
      kind: "price_index",
      country: "US",
      country_name: "미국",
      label: "미국 CPI",
      maturity_months: null,
      unit: "Index 1982-1984=100",
      rows: 7,
      observed_from: "2026-02-01",
      observed_to: "2026-07-01",
    },
  ],
};

const POINTS: IndicatorPoints = {
  provider: "fred",
  series_id: "DGS10",
  kind: "government_bond",
  label: "미국 10년물",
  unit: "Percent",
  points: 2,
  dates: ["2026-08-24", "2026-08-25"],
  values: [4.7, 4.72],
};

const CURVE: CurveResponse = {
  as_of: "2026-08-27",
  countries: [
    {
      provider: "fred",
      country: "US",
      country_name: "미국",
      unit: "Percent",
      points: [
        { series_id: "DGS2", maturity_months: 24, label: "미국 2년물", observation_date: "2026-08-25", value: 4.17 },
        { series_id: "DGS10", maturity_months: 120, label: "미국 10년물", observation_date: "2026-08-25", value: 4.64 },
      ],
    },
    {
      provider: "mof",
      country: "JP",
      country_name: "일본",
      unit: "Percent",
      points: [
        { series_id: "JGB10Y", maturity_months: 120, label: "일본 10년물", observation_date: "2026-08-22", value: 2.9 },
      ],
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

it("kind 필터가 요청에 실린다", async () => {
  const log = stubFetch({ "/api/indicators/series": SERIES });
  renderAt("/indicators?kind=government_bond", "/indicators", <IndicatorsPage />);

  await screen.findByRole("table");
  expect(log.paths[0]).toBe("/api/indicators/series?kind=government_bond");
});

it("만기 없는 계열은 0이 아니라 —로 보인다", async () => {
  // 0으로 채우면 만기별 비교가 그 시계열을 "0개월물"로 그린다.
  stubFetch({ "/api/indicators/series": SERIES });
  renderAt("/indicators", "/indicators", <IndicatorsPage />);

  await screen.findByRole("table");
  const row = screen.getByRole("row", { name: /CPI_M/ });
  expect(row.textContent).toContain("—");
  expect(row.textContent).toContain("Index 1982-1984=100");
});

it("단위가 다른 계열이 한 목록에서도 구분된다", async () => {
  stubFetch({ "/api/indicators/series": SERIES });
  renderAt("/indicators", "/indicators", <IndicatorsPage />);

  await screen.findByRole("table");
  expect(screen.getByRole("row", { name: /DGS10/ }).textContent).toContain("Percent");
  expect(screen.getByRole("row", { name: /CPI_M/ }).textContent).not.toContain("Percent");
});

it("관측값 요청은 provider와 series_id를 함께 싣는다", async () => {
  // `series_id` 하나로 걸면 제공처가 늘 때 조용히 틀린다.
  const log = stubFetch({ "/api/indicators/observations": POINTS });
  renderAt("/indicators/fred/DGS10", "/indicators/:provider/:seriesId", <IndicatorDetailPage />);

  await screen.findByRole("table");
  expect(log.paths[0]).toContain("provider=fred");
  expect(log.paths[0]).toContain("series_id=DGS10");
});

it("축 단위는 응답의 unit이 정한다", async () => {
  stubFetch({ "/api/indicators/observations": POINTS });
  renderAt("/indicators/fred/DGS10", "/indicators/:provider/:seriesId", <IndicatorDetailPage />);

  await screen.findByRole("table");
  expect(screen.getByText(/Percent · 기준 2026-08-25/)).toBeTruthy();
});

it("곡선은 만기 순으로 보이고 나라마다 관측일이 다를 수 있다", async () => {
  stubFetch({ "/api/indicators/curve": CURVE });
  renderAt("/indicators/curve", "/indicators/curve", <CurvePage />);

  await screen.findByRole("table");
  const rows = [...document.querySelectorAll("tbody tr")].map((node) => node.textContent ?? "");
  expect(rows[0]).toContain("2년");
  expect(rows[1]).toContain("10년");
  // 일본은 마지막 고시일이 다르다. 그것이 사실이라 감추지 않는다.
  expect(rows[2]).toContain("2026-08-22");
});

it("빈 곡선은 그렇게 말한다", async () => {
  stubFetch({ "/api/indicators/curve": { as_of: "2026-08-27", countries: [] } });
  renderAt("/indicators/curve", "/indicators/curve", <CurvePage />);

  expect(await screen.findByText(/이 기준일에 곡선이 없다/)).toBeTruthy();
});

it("만기 라벨은 개월과 년을 가른다", () => {
  expect(maturityLabel(3)).toBe("3개월");
  expect(maturityLabel(120)).toBe("10년");
  expect(maturityLabel(null)).toBe("—");
});
