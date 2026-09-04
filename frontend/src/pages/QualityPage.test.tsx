// 품질 화면. **표 둘이 서로 다른 판을 키로 쓴다는 것이 이 파일의 주제다.**

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { QUALITY } from "../fixtures";
import { renderAt, stubFetch } from "../test-harness";
import QualityPage from "./QualityPage";

afterEach(() => vi.unstubAllGlobals());

function tables() {
  return screen.getAllByRole("table");
}

it("전망의 판을 바꿔도 관찰 표의 행이 갈라지지 않는다", async () => {
  stubFetch({ "/api/forecasts/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "전망" });
  const [forecast, review] = tables();
  // 전망 표에는 판 둘, 관찰 표에는 하나다.
  expect(forecast!.querySelectorAll("tbody tr").length).toBe(2);
  expect(review!.querySelectorAll("tbody tr").length).toBe(1);
});

it("관찰 표에는 슬롯이 없다 — 관찰은 하루에 한 번이다", async () => {
  stubFetch({ "/api/forecasts/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "전망" });
  const [forecast, review] = tables();

  expect(forecast!.textContent).toContain("장전");
  expect(review!.textContent).not.toContain("장전");
});

it("표본 수는 비율 옆에 함께 보인다", async () => {
  stubFetch({ "/api/forecasts/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "전망" });
  const row = tables()[0]!.querySelectorAll("tbody tr")[0]!;

  // 4건에서 나온 75%가 100건처럼 읽히면 안 된다.
  expect(row.textContent).toContain("n=4 (표본 부족)");
  expect(row.textContent).toContain("75.0%");
});

it("채점 0건의 비율을 0%로 바꾸지 않는다", async () => {
  stubFetch({ "/api/forecasts/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "전망" });
  const empty = tables()[0]!.querySelectorAll("tbody tr")[1]!;

  expect(empty.textContent).toContain("—");
  expect(empty.textContent).not.toContain("0.0%");
});

it("적중·밴드·오차를 합친 점수가 없다", async () => {
  stubFetch({ "/api/forecasts/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "전망" });
  const headers = [...document.querySelectorAll("th")].map((node) => node.textContent ?? "");

  // 서로 다른 것을 재고 단위도 다르다.
  expect(headers).not.toContain("종합");
  expect(headers).toContain("방향");
  expect(headers).toContain("밴드");
  expect(headers).toContain("평균 오차");
});

it("찍기 대비는 넘음·못 넘음으로만 말한다", async () => {
  stubFetch({ "/api/forecasts/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "전망" });
  expect(screen.getByText("넘음")).toBeTruthy();
  // 표본이 없는 행은 판단하지 않는다 — `못 넘음`으로 채우지 않는다.
  expect(screen.queryByText("못 넘음")).toBeNull();
});

it("폭이 오차를 못 덮으면 그렇게 적는다", async () => {
  stubFetch({ "/api/forecasts/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "전망" });
  // 평균 오차 1.5%p에 평균 폭 1.4%p — 구조적으로 못 맞히는 폭이다.
  expect(screen.getByText("부족")).toBeTruthy();
});

it("표본 0이면 빈 상태를 표 대신 보인다", async () => {
  stubFetch({
    "/api/forecasts/quality": { forecast: [], review: [], coin_flip_hit_rate: 0.5 },
  });
  renderAt("/quality?slot=midday", "/quality", <QualityPage />);

  expect(await screen.findByText("이 조건에 전망이 없다.")).toBeTruthy();
  expect(screen.getByText("이 조건에 관찰이 없다.")).toBeTruthy();
});

it("필터가 URL을 통해 요청까지 간다", async () => {
  const log = stubFetch({ "/api/forecasts/quality": QUALITY });
  renderAt("/quality?slot=pre_open", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "전망" });
  expect(log.paths[0]).toBe("/api/forecasts/quality?slot=pre_open");

  await userEvent.selectOptions(screen.getByLabelText("슬롯"), "midday");
  await screen.findByRole("heading", { name: "전망" });
  expect(log.paths[1]).toBe("/api/forecasts/quality?slot=midday");
});
