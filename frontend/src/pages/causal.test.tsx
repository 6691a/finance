// 인과 그래프 화면.
//
// 주제 셋: ① 체인이 사건에서 대상 순서로 한 줄에 보인다 ② 등락 단위가 행마다 붙는다
// ③ 이 화면이 인과의 증명이 아니라는 말이 화면에 있다.

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { renderAt, stubFetch } from "../test-harness";
import CausalPage, { chainText, changeText } from "./CausalPage";

afterEach(() => vi.unstubAllGlobals());

const PATH = {
  id: 1,
  week_start: "2026-08-10",
  source_kind: "event",
  event_id: 1,
  event_title: "미국 7월 소비자물가 상승률 둔화",
  event_occurred_on: "2026-08-12",
  source_target_kind: null,
  source_target_code: null,
  source_sign: null,
  target_kind: "quote",
  target_code: "US10Y",
  channels: ["통화정책 기대", "할인율"],
  sign: "down",
  confidence: "observed",
  reasoning: "물가 둔화로 긴축 우려가 낮아졌다.",
  return_week_change: -0.0638,
  return_t1_change: 0.5962,
  return_t5_change: 0.8944,
  return_unit: "percent",
  llm_run_id: null,
};

const PATHS = { items: [PATH], limit: 50, offset: 0, has_more: false };

it("체인이 사건에서 대상 순서로 한 줄에 보인다", async () => {
  stubFetch({ "/api/causal/paths": PATHS });
  renderAt("/causal", "/causal", <CausalPage />);

  await screen.findByRole("table");
  expect(
    screen.getByText("미국 7월 소비자물가 상승률 둔화 → 통화정책 기대 → 할인율 → US10Y"),
  ).toBeTruthy();
});

it("등락 단위는 행이 정한다", () => {
  // `KTB10Y`의 7bp와 KOSPI의 10%가 한 칸에 들어가면 크기 비교가 조용히 무의미해진다.
  expect(changeText(-0.0638, "percent")).toBe("-0.06%");
  expect(changeText(7.4, "basis_point")).toBe("7.4bp");
});

it("체인은 채널이 없어도 사건과 대상을 잇는다", () => {
  expect(chainText({ ...PATH, channels: [] })).toBe("미국 7월 소비자물가 상승률 둔화 → US10Y");
});

it("인과의 증명이 아니라는 말이 화면에 있다", async () => {
  // 표만 보면 화살표가 인과로 읽힌다. `observed`는 함께 관찰됐다는 뜻뿐이다.
  stubFetch({ "/api/causal/paths": PATHS });
  renderAt("/causal", "/causal", <CausalPage />);

  await screen.findByRole("table");
  expect(document.body.textContent).toContain("인과의 증명이 아니다");
  expect(screen.getByRole("cell", { name: "문서가 말함" })).toBeTruthy();
});

it("데이터셋을 바꾸면 사건·채널 경로를 부른다", async () => {
  const log = stubFetch({
    "/api/causal/paths": PATHS,
    "/api/causal/events": { items: [], limit: 50, offset: 0, has_more: false },
    "/api/causal/channels": { items: [], limit: 50, offset: 0, has_more: false },
  });
  renderAt("/causal", "/causal", <CausalPage />);
  await screen.findByRole("table");

  await userEvent.click(screen.getByRole("radio", { name: "사건" }));
  await screen.findByText(/사건이 없다/);

  await userEvent.click(screen.getByRole("radio", { name: "채널" }));
  await screen.findByText(/채널이 없다/);

  expect(log.paths.some((path) => path.startsWith("/api/causal/events"))).toBe(true);
  expect(log.paths.some((path) => path.startsWith("/api/causal/channels"))).toBe(true);
});

it("경로를 누르면 상세로 간다", async () => {
  stubFetch({ "/api/causal/paths": PATHS });
  renderAt("/causal", "/causal", <CausalPage />);

  await screen.findByRole("table");
  const link = screen.getByRole("link", {
    name: "미국 7월 소비자물가 상승률 둔화 → 통화정책 기대 → 할인율 → US10Y",
  });
  expect(link.getAttribute("href")).toBe("/causal/1");
});

it("대상에서 출발한 경로는 원인의 방향까지 보인다", async () => {
  // **원인이 우리가 가진 값이라 방향이 있어야 뜻이 산다** — `US10Y`만으로는 올라서인지
  // 내려서인지 알 수 없다.
  const fromTarget = {
    ...PATH,
    id: 51,
    source_kind: "target",
    event_id: null,
    event_title: null,
    event_occurred_on: null,
    source_target_kind: "quote",
    source_target_code: "US10Y",
    source_sign: "down",
    target_kind: "instrument",
    target_code: "005930",
    channels: ["할인율"],
    confidence: "endpoint_observed",
    sign: "up",
  };
  stubFetch({
    "/api/causal/paths": { items: [fromTarget], limit: 50, offset: 0, has_more: false },
  });
  renderAt("/causal", "/causal", <CausalPage />);

  await screen.findByRole("table");
  expect(screen.getByRole("link", { name: "US10Y(하락) → 할인율 → 005930" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "대상" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "양 끝 값" })).toBeTruthy();
});

it("방향성은 세기와 종합을 함께 보인다", async () => {
  // **방향은 LLM이 정하고 세기는 코드가 센다.** 둘이 어긋나는 것이 정상이라 둘 다 보인다 —
  // 위로 4, 아래로 2인데 종합이 `엇갈림`인 행이 실제로 있다(2026-08-31 운영).
  const directions = {
    items: [
      {
        week_start: "2026-08-17",
        target_kind: "index",
        target_code: "KOSPI",
        bias: "mixed",
        reasoning: "자사주와 성장 기대가 받쳤지만 미국 국가부채가 눌러 엇갈렸다.",
        up_count: 4,
        down_count: 2,
        flat_count: 0,
        path_ids: [59, 68, 73],
        channels: [{ name: "투자심리", up: 3, down: 2 }],
        llm_run_id: 12,
      },
    ],
    limit: 50,
    offset: 0,
    has_more: false,
  };
  stubFetch({ "/api/causal/directions": directions });
  renderAt("/causal?dataset=directions", "/causal", <CausalPage />);

  await screen.findByRole("table");
  expect(screen.getByRole("cell", { name: "엇갈림" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "4 / 2" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "투자심리 3↑2↓" })).toBeTruthy();
});
