// 실행·툴·전망 화면.
//
// 주제 넷: ① URL query filter가 실제 요청까지 간다 ② running·성공·실패 실행이 각각 다르게
// 읽힌다 ③ 전망의 이유가 근거로 링크된다 ④ 악성 문자열이 text로 보인다.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import {
  ACCURACY,
  CALL_DETAIL,
  FAILED_CALL,
  FORECAST_DETAIL,
  FORECAST_LIST,
  PLAIN_CALL_DETAIL,
  RUN_DETAIL,
  RUN_LIST,
} from "../fixtures";
import { renderAt, stubFetch } from "../test-harness";
import ForecastDetailPage from "./ForecastDetailPage";
import ForecastPage from "./ForecastPage";
import RunDetailPage from "./RunDetailPage";
import RunsPage from "./RunsPage";
import ToolCallPage from "./ToolCallPage";

afterEach(() => vi.unstubAllGlobals());

it("URL의 필터가 첫 요청에 그대로 실린다", async () => {
  const log = stubFetch({ "/api/llm-runs": RUN_LIST });
  renderAt("/runs?from=2026-08-20&to=2026-08-21&status=failed&kind=review", "/runs", <RunsPage />);

  await screen.findByRole("table");
  // 새로고침·뒤로 가기·링크 공유가 같은 요청을 만든다.
  expect(log.paths[0]).toBe("/api/llm-runs?from=2026-08-20&to=2026-08-21&kind=review&status=failed");
});

it("필터를 바꾸면 새 요청이 나가고 쪽은 처음으로 돌아간다", async () => {
  const log = stubFetch({ "/api/llm-runs": RUN_LIST });
  renderAt("/runs?offset=50", "/runs", <RunsPage />);
  await screen.findByRole("table");

  await userEvent.selectOptions(screen.getByLabelText("상태"), "failed");

  await waitFor(() => expect(log.paths.length).toBe(2));
  expect(log.paths[1]).toBe("/api/llm-runs?status=failed");
});

it("종료를 기록하지 못한 실행은 소요가 비어 있고 그렇게 적힌다", async () => {
  stubFetch({ "/api/llm-runs": RUN_LIST });
  renderAt("/runs", "/runs", <RunsPage />);

  await screen.findByRole("table");
  const row = screen.getByRole("row", { name: /종료 미기록/ });
  // 시작 후 경과를 지금 시각으로 채우지 않는다 — 조회할 때마다 값이 변한다.
  expect(row.textContent).toContain("—");
});

it("전망 대화의 메모 칸은 0이 아니라 빈 표시다", async () => {
  stubFetch({ "/api/llm-runs": RUN_LIST });
  renderAt("/runs", "/runs", <RunsPage />);

  await screen.findByRole("table");
  // 관찰 대화만 값이 있다. 0으로 채우면 "0건"과 "해당 없음"이 같아 보인다.
  const review = screen.getByRole("row", { name: /장후 관찰/ });
  expect(review.textContent).toContain("+1 유지2 내림1");
});

it("실행 목록에는 툴 인자가 없다", async () => {
  stubFetch({ "/api/llm-runs": RUN_LIST });
  renderAt("/runs", "/runs", <RunsPage />);

  await screen.findByRole("table");
  expect(document.body.textContent).not.toContain("factor_history");
});

it("호출 타임라인은 라운드로 묶고 seq를 인과 순서라고 말하지 않는다", async () => {
  stubFetch({ "/api/llm-runs/9": RUN_DETAIL });
  renderAt("/runs/9", "/runs/:llmRunId", <RunDetailPage />);

  await screen.findByRole("heading", { name: /실행 9/ });
  const captions = [...document.querySelectorAll("caption")].map((node) => node.textContent ?? "");
  expect(captions.some((text) => text.includes("라운드 1"))).toBe(true);
  expect(captions.some((text) => text.includes("라운드 2"))).toBe(true);
  expect(captions.join(" ")).toContain("기록 순서이고");
});

it("모델에게 전달되지 않은 결과는 그렇게 표시한다", async () => {
  stubFetch({ "/api/llm-runs/9": RUN_DETAIL });
  renderAt("/runs/9", "/runs/:llmRunId", <RunDetailPage />);

  await screen.findByRole("heading", { name: /실행 9/ });
  expect(screen.getByText("성공 · 모델에게 전달되지 않음")).toBeTruthy();
});

it("실패한 호출은 오류 종류를 함께 보인다", async () => {
  stubFetch({ "/api/llm-runs/9": RUN_DETAIL });
  renderAt("/runs/9", "/runs/:llmRunId", <RunDetailPage />);

  await screen.findByRole("heading", { name: /실행 9/ });
  expect(screen.getByText(`실패 · ${FAILED_CALL.error_kind}`)).toBeTruthy();
});

it("실패한 실행은 실패 전 기록과 마지막 오류를 보이고 산출물은 비어 있다", async () => {
  stubFetch({
    "/api/llm-runs/12": {
      ...RUN_DETAIL,
      id: 12,
      status: "failed",
      error: "모델이 붙지 않았다",
      produced_forecasts: [],
    },
  });
  renderAt("/runs/12", "/runs/:llmRunId", <RunDetailPage />);

  await screen.findByRole("heading", { name: /실행 12/ });
  expect(screen.getByText("실패 사유: 모델이 붙지 않았다")).toBeTruthy();
  expect(screen.getByText("이 실행이 남긴 전망이 없다.")).toBeTruthy();
});

it("관찰 대화는 전망이 없는 것을 실패로 말하지 않는다", async () => {
  stubFetch({
    "/api/llm-runs/13": { ...RUN_DETAIL, id: 13, kind: "review", slot: null, produced_forecasts: [] },
  });
  renderAt("/runs/13", "/runs/:llmRunId", <RunDetailPage />);

  await screen.findByRole("heading", { name: /실행 13/ });
  expect(screen.getByText(/그래프와 메모에만 쓴다/)).toBeTruthy();
});

it("running 실행은 기록이 완전하다고 주장하지 않는다", async () => {
  stubFetch({
    "/api/llm-runs/11": { ...RUN_DETAIL, id: 11, status: "running", finished_at: null, duration_ms: null },
  });
  renderAt("/runs/11", "/runs/:llmRunId", <RunDetailPage />);

  await screen.findByRole("heading", { name: /실행 11/ });
  expect(screen.getByText(/종료를 기록하지 못한 실행이다/)).toBeTruthy();
  expect(screen.getByText("— (종료 미기록)")).toBeTruthy();
});

it("툴 결과는 호출 하나를 골랐을 때만 온다", async () => {
  const log = stubFetch({ "/api/llm-runs/9/tool-calls/1": CALL_DETAIL, "/api/llm-runs/9": RUN_DETAIL });
  renderAt("/runs/9", "/runs/:llmRunId", <RunDetailPage />);

  await screen.findByRole("heading", { name: /실행 9/ });
  // 상세를 열었을 뿐인데 결과 전문을 미리 가져오지 않는다.
  expect(log.paths).toEqual(["/api/llm-runs/9"]);
});

it("원 인자와 검증된 인자가 나란히 보인다", async () => {
  stubFetch({ "/api/llm-runs/9/tool-calls/1": CALL_DETAIL });
  renderAt("/runs/9/tool-calls/1", "/runs/:llmRunId/tool-calls/:seq", <ToolCallPage />);

  await screen.findByRole("heading", { name: /factor_history/ });
  expect(screen.getByRole("heading", { name: "모델이 보낸 인자" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "검증 뒤 실제 인자" })).toBeTruthy();
});

it("함수에 진입하지 못한 호출은 검증된 인자가 비어 있다고 말한다", async () => {
  stubFetch({ "/api/llm-runs/9/tool-calls/3": { ...FAILED_CALL, result: null } });
  renderAt("/runs/9/tool-calls/3", "/runs/:llmRunId/tool-calls/:seq", <ToolCallPage />);

  await screen.findByRole("heading", { name: /factor_history/ });
  expect(screen.getByText(/함수에 진입하지 못했다/)).toBeTruthy();
  // 실패 호출은 결과와 오류를 동시에 보이지 않는다.
  expect(screen.queryByRole("heading", { name: "결과" })).toBeNull();
  expect(screen.getByRole("heading", { name: "오류" })).toBeTruthy();
});

it("JSON 결과는 들여쓰고 평문은 원문 그대로 둔다", async () => {
  stubFetch({ "/api/llm-runs/9/tool-calls/1": CALL_DETAIL });
  const view = renderAt("/runs/9/tool-calls/1", "/runs/:llmRunId/tool-calls/:seq", <ToolCallPage />);
  await screen.findByRole("heading", { name: "결과" });
  expect(document.body.textContent).toContain('"rows": [\n    1,\n    2\n  ]');
  view.unmount();

  stubFetch({ "/api/llm-runs/9/tool-calls/4": PLAIN_CALL_DETAIL });
  renderAt("/runs/9/tool-calls/4", "/runs/:llmRunId/tool-calls/:seq", <ToolCallPage />);
  await screen.findByRole("heading", { name: "결과" });
  // 파싱 실패는 화면 오류가 아니다.
  expect(screen.getByText(/not json <script>alert\(1\)<\/script>/)).toBeTruthy();
});

// --- 전망 화면 ---------------------------------------------------------------

it("그 날짜의 슬롯 셋을 한 화면에 세로로 둔다", async () => {
  const log = stubFetch({ "/api/forecasts": FORECAST_LIST, "/api/forecasts/accuracy": ACCURACY });
  renderAt("/forecast?day=2026-09-03", "/forecast", <ForecastPage />);

  await screen.findByRole("heading", { name: /장전/ });
  expect(log.paths[0]).toBe("/api/forecasts?from=2026-09-03&to=2026-09-03");
  // 셋이 같은 화면에 있어야 "장전이 틀렸다는 걸 장중이 인정했나"가 읽힌다.
  expect(screen.getByRole("heading", { name: /장중/ })).toBeTruthy();
  expect(screen.getByRole("heading", { name: /마감전/ })).toBeTruthy();
});

it("장전 슬롯에는 '여기까지'가 없다 — 아직 안 열렸다", async () => {
  stubFetch({ "/api/forecasts": FORECAST_LIST, "/api/forecasts/accuracy": ACCURACY });
  renderAt("/forecast?day=2026-09-03", "/forecast", <ForecastPage />);

  await screen.findByRole("heading", { name: /장전/ });
  const panels = [...document.querySelectorAll(".panel")];
  const preOpen = panels.find((node) => node.textContent?.includes("장전"));
  expect(preOpen?.textContent).toContain("전일 종가");
});

it("채점 전은 '채점 대기'이지 빈 칸이 아니다", async () => {
  stubFetch({ "/api/forecasts": FORECAST_LIST, "/api/forecasts/accuracy": ACCURACY });
  renderAt("/forecast?day=2026-09-03", "/forecast", <ForecastPage />);

  await screen.findByRole("heading", { name: /장중/ });
  expect(screen.getAllByText("채점 대기").length).toBeGreaterThan(0);
  // 채점된 행은 방향과 밴드를 함께 말한다.
  expect(screen.getByText(/방향 ○ · 밴드 밖/)).toBeTruthy();
});

it("적중률은 표본 수와 함께 보인다", async () => {
  stubFetch({ "/api/forecasts": FORECAST_LIST, "/api/forecasts/accuracy": ACCURACY });
  renderAt("/forecast?day=2026-09-03", "/forecast", <ForecastPage />);

  await screen.findByRole("heading", { name: /장중/ });
  // 3건에서 나온 60%가 100건처럼 읽히면 안 된다.
  expect(screen.getByText(/최근 채점 5건/)).toBeTruthy();
});

it("이유마다 그 근거로 가는 링크가 붙는다", async () => {
  stubFetch({ "/api/forecasts/2026-09-03/midday": FORECAST_DETAIL });
  renderAt("/forecast/2026-09-03/midday", "/forecast/:runDate/:slot", <ForecastDetailPage />);

  await screen.findByRole("heading", { name: /2026-09-03/ });
  const hrefs = [...document.querySelectorAll("a")].map((node) => node.getAttribute("href") ?? "");
  expect(hrefs).toContain("/relations/FOREIGN_NET_BUY");
  expect(hrefs).toContain("/memories?id=17");
  expect(hrefs).toContain("/forecast/2026-09-03/pre_open");
  // 셋 다 없는 이유는 링크가 아니라 글자다.
  expect(screen.getByText("관측 상태")).toBeTruthy();
});

it("관측 상태는 표가 아니라 접힌 JSON이다", async () => {
  stubFetch({ "/api/forecasts/2026-09-03/midday": FORECAST_DETAIL });
  renderAt("/forecast/2026-09-03/midday", "/forecast/:runDate/:slot", <ForecastDetailPage />);

  await screen.findByRole("heading", { name: /2026-09-03/ });
  // 모양이 판마다 바뀌므로 화면이 그 모양을 알면 안 된다.
  expect(document.querySelector("details")).toBeTruthy();
  expect(document.querySelector("pre")?.textContent).toContain('"run_date": "2026-09-03"');
});

it("악성 문자열은 text로 보이고 DOM에 심어지지 않는다", async () => {
  stubFetch({ "/api/forecasts/2026-09-03/midday": FORECAST_DETAIL });
  renderAt("/forecast/2026-09-03/midday", "/forecast/:runDate/:slot", <ForecastDetailPage />);

  await screen.findByRole("heading", { name: /2026-09-03/ });
  expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
  expect(document.querySelector("img")).toBeNull();
});
