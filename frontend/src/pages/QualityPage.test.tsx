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

it("원 추론의 판을 바꿔도 해설 표의 행이 갈라지지 않는다", async () => {
  stubFetch({ "/api/theses/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "예측 품질" });
  const [forecast, narrative] = tables();
  // 예측 표에는 판 둘, 해설 표에는 하나다.
  expect(forecast!.querySelectorAll("tbody tr").length).toBe(2);
  expect(narrative!.querySelectorAll("tbody tr").length).toBe(1);
});

it("해설 표의 판은 예측 표에 나타나지 않는다", async () => {
  stubFetch({ "/api/theses/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "예측 품질" });
  const [forecast, narrative] = tables();

  expect(forecast!.textContent).not.toContain("2/informed");
  expect(narrative!.textContent).toContain("2/informed");
  expect(narrative!.textContent).not.toContain("pre_open");
});

it("표본 수는 metric마다 따로 보인다", async () => {
  stubFetch({ "/api/theses/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "예측 품질" });
  const row = tables()[0]!.querySelectorAll("tbody tr")[0]!;

  // Brier 12건, 크기 오차 4건, 실행 3건이 한 행 안에서 각자 보인다.
  expect(row.textContent).toContain("n=12");
  expect(row.textContent).toContain("n=4 (표본 부족)");
  expect(row.textContent).toContain("n=3 (표본 부족)");
});

it("null metric을 0으로 바꾸지 않는다", async () => {
  stubFetch({ "/api/theses/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "예측 품질" });
  const empty = tables()[0]!.querySelectorAll("tbody tr")[1]!;

  expect(empty.textContent).toContain("—");
  expect(empty.textContent).not.toContain("0.000");
});

it("Brier·크기 오차·판정을 합친 점수가 없다", async () => {
  stubFetch({ "/api/theses/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "예측 품질" });
  const headers = [...document.querySelectorAll("th")].map((node) => node.textContent ?? "");

  expect(headers).not.toContain("종합");
  expect(headers.filter((text) => text.includes("Brier")).length).toBe(2);
});

it("baseline은 통과·미달로만 말한다", async () => {
  stubFetch({ "/api/theses/quality": QUALITY });
  renderAt("/quality", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "예측 품질" });
  expect(screen.getByText("통과")).toBeTruthy();
  // 표본이 없는 행은 판단하지 않는다 — `미달`로 채우지 않는다.
  expect(screen.queryByText("미달")).toBeNull();
});

it("표본 0이면 빈 상태와 필터 초기화가 보인다", async () => {
  stubFetch({ "/api/theses/quality": { forecast: [], narrative: [], uniform_brier: 0.6666666666666666 } });
  renderAt("/quality?subject_code=NOPE", "/quality", <QualityPage />);

  expect(await screen.findByText("이 조건에 채점된 추론이 없다.")).toBeTruthy();
  expect(screen.getByText("이 조건에 판정이 붙은 해설이 없다.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "필터 초기화" })).toBeTruthy();
});

it("필터가 URL을 통해 요청까지 간다", async () => {
  const log = stubFetch({ "/api/theses/quality": QUALITY });
  renderAt("/quality?slot=pre_open&horizon_days=0", "/quality", <QualityPage />);

  await screen.findByRole("heading", { name: "예측 품질" });
  expect(log.paths[0]).toBe("/api/theses/quality?slot=pre_open&horizon_days=0");

  await userEvent.selectOptions(screen.getByLabelText("지평"), "5");
  await screen.findByRole("heading", { name: "예측 품질" });
  expect(log.paths[1]).toBe("/api/theses/quality?slot=pre_open&horizon_days=5");
});
