// 실행·툴·판단 화면.
//
// 주제 셋: ① URL query filter가 실제 요청까지 간다 ② running·성공·실패 실행이 각각 다르게
// 읽힌다 ③ 악성 문자열이 text로 보인다.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import {
  CALL_DETAIL,
  FAILED_CALL,
  PLAIN_CALL_DETAIL,
  RUN_DETAIL,
  RUN_LIST,
  THESIS_DETAIL,
  THESIS_LIST,
} from "../fixtures";
import { renderAt, stubFetch } from "../test-harness";
import RunDetailPage from "./RunDetailPage";
import RunsPage from "./RunsPage";
import ThesesPage from "./ThesesPage";
import ThesisDetailPage from "./ThesisDetailPage";
import ToolCallPage from "./ToolCallPage";

afterEach(() => vi.unstubAllGlobals());

it("URL의 필터가 첫 요청에 그대로 실린다", async () => {
  const log = stubFetch({ "/api/llm-runs": RUN_LIST });
  renderAt("/runs?from=2026-08-20&to=2026-08-21&status=failed&kind=narration", "/runs", <RunsPage />);

  await screen.findByRole("table");
  // 새로고침·뒤로 가기·링크 공유가 같은 요청을 만든다.
  expect(log.paths[0]).toBe("/api/llm-runs?from=2026-08-20&to=2026-08-21&kind=narration&status=failed");
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

it("실행 목록에는 툴 인자가 없다", async () => {
  stubFetch({ "/api/llm-runs": RUN_LIST });
  renderAt("/runs", "/runs", <RunsPage />);

  await screen.findByRole("table");
  expect(document.body.textContent).not.toContain("recent_documents");
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
    "/api/llm-runs/12": { ...RUN_DETAIL, id: 12, status: "failed", error: "모델이 붙지 않았다", produced_theses: [] },
  });
  renderAt("/runs/12", "/runs/:llmRunId", <RunDetailPage />);

  await screen.findByRole("heading", { name: /실행 12/ });
  expect(screen.getByText("실패 사유: 모델이 붙지 않았다")).toBeTruthy();
  expect(screen.getByText("이 실행이 남긴 산출물이 없다.")).toBeTruthy();
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

  await screen.findByRole("heading", { name: /recent_documents/ });
  expect(screen.getByRole("heading", { name: "모델이 보낸 인자" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "검증 뒤 실제 인자" })).toBeTruthy();
});

it("함수에 진입하지 못한 호출은 검증된 인자가 비어 있다고 말한다", async () => {
  stubFetch({ "/api/llm-runs/9/tool-calls/3": { ...FAILED_CALL, result: null } });
  renderAt("/runs/9/tool-calls/3", "/runs/:llmRunId/tool-calls/:seq", <ToolCallPage />);

  await screen.findByRole("heading", { name: /recent_documents/ });
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

it("악성 문자열은 text로 보이고 DOM에 심어지지 않는다", async () => {
  stubFetch({ "/api/theses/1": THESIS_DETAIL, "/api/llm-runs/9/tool-calls/4": PLAIN_CALL_DETAIL });
  renderAt("/theses/1", "/theses/:thesisId", <ThesisDetailPage />);

  await screen.findByRole("heading", { name: /코스피/ });
  expect(screen.getByText(/<script>alert\(1\)<\/script> 반도체 수출 증가/)).toBeTruthy();
  // 이유·툴 결과·근거 제목 셋 다 확인한다. 심어졌으면 여기서 요소가 잡힌다.
  expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
  expect(document.querySelector("img")).toBeNull();
});

it("javascript: 근거 URL은 anchor가 되지 않는다", async () => {
  stubFetch({ "/api/theses/1": THESIS_DETAIL });
  renderAt("/theses/1", "/theses/:thesisId", <ThesisDetailPage />);

  await screen.findByRole("heading", { name: /코스피/ });
  const hrefs = [...document.querySelectorAll("a")].map((node) => node.getAttribute("href") ?? "");
  expect(hrefs.some((href) => href.startsWith("javascript:"))).toBe(false);
  // 원문은 버리지 않고 text로 남긴다.
  expect(document.body.textContent).toContain("javascript:alert(1)");
});

it("외부 링크에는 noopener noreferrer가 붙는다", async () => {
  stubFetch({ "/api/theses/1": THESIS_DETAIL });
  renderAt("/theses/1", "/theses/:thesisId", <ThesisDetailPage />);

  await screen.findByRole("heading", { name: /코스피/ });
  const external = screen.getByRole("link", { name: /반도체 수출 증가/ });
  expect(external.getAttribute("rel")).toBe("noopener noreferrer");
});

it("세 방향 이유를 모두 보이고 숨은 사고를 지어내지 않는다", async () => {
  stubFetch({ "/api/theses/1": THESIS_DETAIL });
  renderAt("/theses/1", "/theses/:thesisId", <ThesisDetailPage />);

  await screen.findByRole("heading", { name: "명시적 판단 이유" });
  expect(screen.getByText(/수출이 늘었다/)).toBeTruthy();
  expect(screen.getByText("금리가 올랐다")).toBeTruthy();
  expect(screen.getByText("둘이 상쇄된다")).toBeTruthy();
});

it("원장 도입 전 판단도 화면 전체를 실패시키지 않는다", async () => {
  stubFetch({ "/api/theses/1": { ...THESIS_DETAIL, llm_run: null } });
  renderAt("/theses/1", "/theses/:thesisId", <ThesisDetailPage />);

  expect(await screen.findByText("실행 원장 도입 전 기록이다.")).toBeTruthy();
});

it("채점이 없으면 평균 Brier가 0이 아니라 빈 표시다", async () => {
  stubFetch({ "/api/theses": THESIS_LIST });
  renderAt("/theses", "/theses", <ThesesPage />);

  await screen.findByRole("table");
  const row = screen.getByRole("row", { name: /코스피/ });
  expect(row.textContent).not.toContain("0.000");
});

it("판단 목록의 필터도 URL에서 온다", async () => {
  const log = stubFetch({ "/api/theses": THESIS_LIST });
  renderAt("/theses?slot=pre_open&subject_code=KOSPI", "/theses", <ThesesPage />);

  await screen.findByRole("table");
  expect(log.paths[0]).toBe("/api/theses?slot=pre_open&subject_code=KOSPI");
});
