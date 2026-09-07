// 시세 화면.
//
// 주제 셋: ① URL 필터가 요청까지 간다 ② **KRX와 NXT를 합치지 않는다** ③ 상한을 넘는
// 조합은 고를 수 없다.

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import type { BarSeries, DailySeries, QuoteSymbolList } from "../types";
import { renderAt, stubFetch } from "../test-harness";

// jsdom에는 canvas가 없다. 차트 lifecycle은 `ChartView.test.tsx`가 본다.
// `ResizeObserver`는 `setup-tests.ts`가 폴리필로 채운다.
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

const { default: QuotesPage } = await import("./QuotesPage");
const { default: QuoteDetailPage, estimatePoints, MAX_POINTS } = await import("./QuoteDetailPage");

const SYMBOLS: QuoteSymbolList = {
  items: [
    {
      kind: "index",
      symbol: "KOSPI",
      provider: "kis",
      label: "코스피",
      country: "KR",
      country_name: "대한민국",
      exchanges: [],
      bar_rows: 3120,
      bar_from: "2026-08-17T00:30:00Z",
      bar_to: "2026-08-27T05:15:00Z",
      daily_rows: 2459,
      daily_from: "2016-08-16",
      daily_to: "2026-08-26",
    },
    {
      kind: "equity",
      symbol: "005930",
      provider: "kis",
      label: "삼성전자",
      country: "KR",
      country_name: "대한민국",
      exchanges: ["KRX", "NXT"],
      bar_rows: 267591,
      bar_from: "2025-08-18T00:00:00Z",
      bar_to: "2026-08-27T05:19:00Z",
      daily_rows: 1892,
      daily_from: "2018-12-10",
      daily_to: "2026-08-26",
    },
    {
      kind: "rate",
      symbol: "US10Y",
      provider: "yahoo",
      label: "미 10년물",
      country: "US",
      country_name: "미국",
      exchanges: [],
      bar_rows: 0,
      bar_from: null,
      bar_to: null,
      daily_rows: 2521,
      daily_from: "2016-08-15",
      daily_to: "2026-08-26",
    },
  ],
};

const BARS: BarSeries = {
  kind: "index",
  symbol: "KOSPI",
  exchange: null,
  provider: "kis",
  interval: "5m",
  points: 2,
  times: ["2026-08-27T00:00:00Z", "2026-08-27T00:05:00Z"],
  open: [3200, 3205],
  high: [3206, 3212],
  low: [3199, 3203],
  close: [3205, 3210],
  volume: [1000, null],
  settled: [],
};

const DAILY: DailySeries = {
  kind: "index",
  symbol: "KOSPI",
  exchange: null,
  provider: "yahoo",
  points: 1,
  dates: ["2026-08-26"],
  open: [3100],
  high: [3150],
  low: [3080],
  close: [3140],
  volume: [null],
  contracts: [],
};

afterEach(() => vi.unstubAllGlobals());

it("심볼 목록이 실제로 쌓인 구간을 보인다", async () => {
  stubFetch({ "/api/quotes/symbols": SYMBOLS });
  renderAt("/quotes", "/quotes", <QuotesPage />);

  await screen.findByRole("table");
  const row = screen.getByRole("row", { name: /KOSPI/ });
  expect(row.textContent).toContain("2016-08-16 → 2026-08-26");
});

it("수집이 안 도는 심볼은 0과 —로 보인다", async () => {
  // 마스터에만 있고 0건인 심볼이 실제로 있다. 숨기면 화면이 빈 기간을 고른다.
  stubFetch({ "/api/quotes/symbols": SYMBOLS });
  renderAt("/quotes", "/quotes", <QuotesPage />);

  await screen.findByRole("table");
  const row = screen.getByRole("row", { name: /US10Y/ });
  expect(row.textContent).toContain("0");
  expect(row.textContent).toContain("—");
});

it("종목 링크는 거래소와 제공처를 미리 얹는다", async () => {
  stubFetch({ "/api/quotes/symbols": SYMBOLS });
  renderAt("/quotes", "/quotes", <QuotesPage />);

  await screen.findByRole("table");
  const link = screen.getByRole("link", { name: "005930" });
  // **제공처가 늘 붙는다.** 같은 심볼을 둘이 주면(아시아 지수) 상세가 그것 없이는
  // "제공처를 고르세요"에서 멈추고, 어느 제공처의 줄이었는지는 목록만 안다.
  expect(link.getAttribute("href")).toBe("/quotes/equity/005930?provider=kis&exchange=KRX");
});

it("kind 필터는 URL에서 온다", async () => {
  stubFetch({ "/api/quotes/symbols": SYMBOLS });
  renderAt("/quotes?kind=equity", "/quotes", <QuotesPage />);

  await screen.findByRole("table");
  expect(screen.queryByRole("link", { name: "KOSPI" })).toBeNull();
  expect(screen.getByRole("link", { name: "005930" })).toBeTruthy();
});

it("분봉 요청에 URL의 간격이 실린다", async () => {
  const log = stubFetch({ "/api/quotes/bars": BARS });
  renderAt("/quotes/index/KOSPI?interval=15m", "/quotes/:kind/:symbol", <QuoteDetailPage />);

  await screen.findByRole("table");
  // **`paths[0]`을 보지 않는다.** 종목 이름 마스터를 함께 받으므로 순서가 정해져 있지 않다.
  const call = log.paths.find((path) => path.startsWith("/api/quotes/bars"));
  expect(call).toContain("interval=15m");
  expect(call).toContain("kind=index");
});

it("종목은 거래소를 고르기 전에는 조회하지 않는다", async () => {
  // 말없이 한쪽을 고르면 화면이 어느 거래소 값인지 밝히지 못한 채 선을 그린다.
  const log = stubFetch({ "/api/quotes/bars": BARS });
  renderAt("/quotes/equity/005930", "/quotes/:kind/:symbol", <QuoteDetailPage />);

  expect(await screen.findByText(/거래소를 골라야 한다/)).toBeTruthy();
  expect(log.paths.filter((path) => path.startsWith("/api/quotes/bars")).length).toBe(0);
});

it("거래소를 고르면 그 값이 요청에 실린다", async () => {
  const log = stubFetch({ "/api/quotes/bars": { ...BARS, exchange: "NXT" } });
  renderAt(
    "/quotes/equity/005930?exchange=NXT",
    "/quotes/:kind/:symbol",
    <QuoteDetailPage />,
  );

  await screen.findByRole("table");
  expect(log.paths.find((path) => path.startsWith("/api/quotes/bars"))).toContain("exchange=NXT");
});

it("KRX와 NXT를 합친 계열을 만들지 않는다", async () => {
  const log = stubFetch({ "/api/quotes/bars": { ...BARS, exchange: "KRX" } });
  renderAt("/quotes/equity/005930?exchange=KRX", "/quotes/:kind/:symbol", <QuoteDetailPage />);

  await screen.findByRole("table");
  // 요청이 하나이고 거래소가 하나다. 두 거래소를 합치려면 요청이 둘이어야 한다.
  const calls = log.paths.filter((path) => path.startsWith("/api/quotes/bars"));
  expect(calls.length).toBe(1);
  expect(calls[0]).not.toContain("exchange=NXT");
});

it("일봉으로 바꾸면 거래일 축을 쓴다", async () => {
  stubFetch({ "/api/quotes/daily": DAILY });
  renderAt("/quotes/index/KOSPI?mode=daily", "/quotes/:kind/:symbol", <QuoteDetailPage />);

  await screen.findByRole("table");
  expect(screen.getByRole("columnheader", { name: "거래일" })).toBeTruthy();
});

it("지수선물 일봉은 월물을 함께 보인다", async () => {
  // 월물이 바뀌면 가격에 갭이 생긴다. 이 칸이 없으면 그 갭이 급변인지 롤오버인지 못 가른다.
  const future = {
    ...DAILY,
    kind: "index_future",
    symbol: "KOSPI200_FUT",
    contracts: ["A01609"],
  };
  stubFetch({ "/api/quotes/daily": future });
  renderAt(
    "/quotes/index_future/KOSPI200_FUT?mode=daily",
    "/quotes/:kind/:symbol",
    <QuoteDetailPage />,
  );

  await screen.findByRole("table");
  expect(screen.getByRole("columnheader", { name: "월물" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "A01609" })).toBeTruthy();
});

it("월물이 없는 kind에는 그 열이 없다", async () => {
  // 빈 배열이 "월물 개념이 없다"다. 빈 열을 그리면 없는 개념이 있는 것처럼 보인다.
  stubFetch({ "/api/quotes/daily": DAILY });
  renderAt("/quotes/index/KOSPI?mode=daily", "/quotes/:kind/:symbol", <QuoteDetailPage />);

  await screen.findByRole("table");
  expect(screen.queryByRole("columnheader", { name: "월물" })).toBeNull();
});

it("분봉↔일봉을 오갈 때 화면이 죽지 않는다", async () => {
  // **회귀 가드.** `mode`가 먼저 바뀌고 응답은 그다음에 온다. 그 한 렌더 동안 새 mode와
  // 옛 응답이 만나는데, mode로 데이터 모양을 단정하면 `dates`가 undefined라 거기서 죽는다.
  stubFetch({ "/api/quotes/bars": BARS, "/api/quotes/daily": DAILY });
  renderAt("/quotes/index/KOSPI", "/quotes/:kind/:symbol", <QuoteDetailPage />);
  await screen.findByRole("table");

  await userEvent.selectOptions(screen.getByLabelText("축"), "daily");

  // 죽었으면 표도 헤더도 통째로 사라진다.
  expect(await screen.findByRole("columnheader", { name: "거래일" })).toBeTruthy();

  await userEvent.selectOptions(screen.getByLabelText("축"), "bar");

  expect(await screen.findByRole("columnheader", { name: "시각" })).toBeTruthy();
});

it("상한을 넘는 간격은 고를 수 없다", async () => {
  // 400을 화면에서 보는 일이 정상 흐름이 되면 안 된다.
  stubFetch({ "/api/quotes/bars": BARS });
  renderAt("/quotes/index/KOSPI?range=1mo", "/quotes/:kind/:symbol", <QuoteDetailPage />);

  await screen.findByRole("table");
  const options = [...screen.getByLabelText("간격").querySelectorAll("option")];
  const minute = options.find((option) => option.textContent === "1분");
  expect(minute?.hasAttribute("disabled")).toBe(true);
});

it("빈 구간은 그렇게 말한다", async () => {
  stubFetch({ "/api/quotes/bars": { ...BARS, points: 0, times: [], close: [], volume: [] } });
  renderAt("/quotes/index/KOSPI", "/quotes/:kind/:symbol", <QuoteDetailPage />);

  expect(await screen.findByText(/이 구간에 봉이 없다/)).toBeTruthy();
});

it("기간을 바꾸면 새 요청이 나간다", async () => {
  const log = stubFetch({ "/api/quotes/bars": BARS });
  renderAt("/quotes/index/KOSPI", "/quotes/:kind/:symbol", <QuoteDetailPage />);
  await screen.findByRole("table");

  await userEvent.selectOptions(screen.getByLabelText("기간"), "5d");
  await screen.findByRole("table");

  expect(log.paths.length).toBeGreaterThan(1);
});

it("예상 점 수는 최악을 센다", () => {
  // 밤에 거래가 없어 실제로는 이보다 적지만, 막는 쪽은 넉넉해야 한다.
  expect(estimatePoints(60 * 24, 1)).toBe(1440);
  expect(estimatePoints(60 * 24 * 30, 1)).toBeGreaterThan(MAX_POINTS);
  expect(estimatePoints(60 * 24 * 30, 15)).toBeLessThan(MAX_POINTS);
});
