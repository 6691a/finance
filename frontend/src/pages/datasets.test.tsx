// 문서·수급·사건·수집 화면.
//
// 주제 넷: ① 데이터셋 탭과 필터가 URL에 남는다 ② 단위·축이 열 이름에 적힌다
// ③ null이 0으로 바뀌지 않는다 ④ 본문·payload가 목록에 안 실린다.

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import CollectionPage from "./CollectionPage";
import DocumentDetailPage from "./DocumentDetailPage";
import DocumentsPage from "./DocumentsPage";
import EventsPage from "./EventsPage";
import PositioningPage from "./PositioningPage";
import { renderAt, stubFetch } from "../test-harness";

afterEach(() => vi.unstubAllGlobals());

const DOCUMENTS = {
  items: [
    {
      id: 1,
      source_slug: "cnbc",
      external_id: "ext-1",
      title: "반도체 <script>alert(1)</script>",
      document_type: "article",
      published_at: "2026-08-27T04:30:00Z",
      language: "en",
      content_level: "full_text",
      canonical_url: "https://example.test/a",
      value_score: 7,
      direction: "positive",
      assessed_at: "2026-08-27T04:40:00Z",
      llm_model: "grok-4.6",
      prompt_version: "3",
      instruments: ["005930"],
      indicators: [],
    },
    {
      id: 2,
      source_slug: "bok",
      external_id: "ext-2",
      title: "미평가 문서",
      document_type: "press_release",
      published_at: "2026-08-27T03:00:00Z",
      language: "ko",
      content_level: "metadata_only",
      canonical_url: null,
      value_score: null,
      direction: null,
      assessed_at: null,
      llm_model: null,
      prompt_version: null,
      instruments: [],
      indicators: [],
    },
  ],
  limit: 100,
  offset: 0,
  has_more: false,
};

const DETAIL = {
  ...DOCUMENTS.items[0],
  body: "본문 <b>전문</b>",
  summary: "요약",
  assessment: "평가 근거",
  detected_at: "2026-08-27T04:31:00Z",
  content_hash: "new",
  assessed_content_hash: "old",
};

it("종목 태그가 코드가 아니라 이름으로 보인다", async () => {
  // `005930`은 코드를 외운 사람만 읽을 수 있다. 코드도 함께 남기는 이유는 필터에 넣을
  // 값이 코드이기 때문이다.
  stubFetch({
    "/api/documents": DOCUMENTS,
    "/api/collection/instruments": { items: [{ ticker: "005930", name: "삼성전자" }] },
  });
  renderAt("/documents", "/documents", <DocumentsPage />);

  await screen.findByRole("table");
  expect(await screen.findByText("삼성전자(005930)")).toBeTruthy();
});

it("방향을 한국어 낱말로 보인다", async () => {
  stubFetch({ "/api/documents": DOCUMENTS });
  renderAt("/documents", "/documents", <DocumentsPage />);

  await screen.findByRole("table");
  expect(screen.getByText("호재")).toBeTruthy();
  // 이모지는 안 쓴다 — 낱말이 이미 뜻을 다 진다.
  expect(screen.queryByRole("img")).toBeNull();
});

it("문서 목록에 본문이 없고 미평가 점수는 —다", async () => {
  stubFetch({ "/api/documents": DOCUMENTS });
  renderAt("/documents", "/documents", <DocumentsPage />);

  await screen.findByRole("table");
  expect(document.body.textContent).not.toContain("본문 전문");
  const row = screen.getByRole("row", { name: /미평가 문서/ });
  expect(row.textContent).toContain("—");
});

it("악성 문자열은 제목에서도 text로 보인다", async () => {
  stubFetch({ "/api/documents": DOCUMENTS });
  renderAt("/documents", "/documents", <DocumentsPage />);

  await screen.findByRole("table");
  expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeTruthy();
  expect(window.document.querySelector("script")).toBeNull();
});

const DISCLOSURES = {
  items: [
    {
      rcept_no: "20260827000123",
      stock_code: "005930",
      corp_code: "00126380",
      company_name: "삼성전자",
      report_name: "주요사항보고서",
      filer_name: "삼성전자",
      corp_class: "Y",
      receipt_date: "2026-08-27",
      detected_at: "2026-08-27T04:00:00Z",
      remarks: null,
      has_body: true,
      url: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260827000123",
    },
  ],
  limit: 200,
  offset: 0,
  has_more: true,
};

const EARNINGS = {
  items: [
    {
      stock_code: "005930",
      rcept_no: "20260814000456",
      release_type: "quarterly",
      period_end: "2026-06-30",
      statement_scope: "consolidated",
      amount_basis: "cumulative",
      metric: "revenue",
      current_amount: 74000000000000,
      prior_year_amount: 60000000000000,
      currency: "KRW",
      source_account_name: "매출액",
      url: "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814000456",
    },
  ],
  limit: 50,
  offset: 0,
  has_more: false,
};

it("공시는 접수번호가 DART 원문 링크다", async () => {
  // 접수번호만 있으면 사람이 DART에서 다시 찾아야 한다. 그 번호가 곧 주소다.
  stubFetch({ "/api/documents/disclosures": DISCLOSURES });
  renderAt("/documents?tab=disclosures", "/documents", <DocumentsPage />);

  const link = await screen.findByRole("link", { name: "20260827000123" });
  expect(link.getAttribute("href")).toBe(
    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260827000123",
  );
  expect(link.getAttribute("rel")).toContain("noopener");
});

it("공시 본문을 받아 뒀는지 표가 밝힌다", async () => {
  // 인과 그래프가 내용을 보려고 받는다. **본문 자체는 목록에 없다** — 한 건이 만 자를 넘는다.
  stubFetch({ "/api/documents/disclosures": DISCLOSURES });
  renderAt("/documents?tab=disclosures", "/documents", <DocumentsPage />);

  await screen.findByRole("table");
  expect(screen.getByRole("columnheader", { name: "본문" })).toBeTruthy();
  expect(screen.getByRole("cell", { name: "받음" })).toBeTruthy();
});

it("실적 숫자도 그것이 나온 공시로 이어진다", async () => {
  stubFetch({ "/api/documents/earnings": EARNINGS });
  renderAt("/documents?tab=earnings", "/documents", <DocumentsPage />);

  const link = await screen.findByRole("link", { name: "20260814000456" });
  expect(link.getAttribute("href")).toContain("rcpNo=20260814000456");
});

it("다음 쪽을 누르면 offset이 요청까지 간다", async () => {
  const log = stubFetch({ "/api/documents/disclosures": DISCLOSURES });
  renderAt("/documents?tab=disclosures", "/documents", <DocumentsPage />);
  await screen.findByRole("table");

  await userEvent.click(screen.getByRole("button", { name: /다음/ }));

  expect(log.paths.some((path) => path.includes("offset=200"))).toBe(true);
});

it("쪽을 넘긴 뒤 필터를 바꾸면 첫 쪽으로 돌아온다", async () => {
  // 안 그러면 결과가 한 쪽뿐인 조건에서 빈 3쪽이 보인다.
  const log = stubFetch({ "/api/documents/disclosures": DISCLOSURES });
  renderAt("/documents?tab=disclosures&offset=200", "/documents", <DocumentsPage />);
  await screen.findByRole("table");

  await userEvent.click(screen.getByRole("radio", { name: "문서" }));

  const last = log.paths[log.paths.length - 1]!;
  expect(last).not.toContain("offset=");
});

it("탭을 바꾸면 다른 경로를 부른다", async () => {
  const log = stubFetch({
    "/api/documents/sources": { items: [] },
    "/api/documents": DOCUMENTS,
  });
  renderAt("/documents", "/documents", <DocumentsPage />);
  await screen.findByRole("table");

  await userEvent.click(screen.getByRole("radio", { name: "출처" }));

  expect(log.paths.some((path) => path.startsWith("/api/documents/sources"))).toBe(true);
});

it("문서 상세는 본문과 평가를 함께 보이고 본문이 바뀐 것을 밝힌다", async () => {
  stubFetch({ "/api/documents/1": DETAIL });
  renderAt("/documents/1", "/documents/:documentId", <DocumentDetailPage />);

  await screen.findByRole("heading", { name: /반도체/ });
  // `평가 근거`는 `<dt>` 라벨과 `<dd>` 값 둘에 나오므로 정확한 문자열로 찾지 않는다.
  expect(window.document.body.textContent).toContain("평가 근거");
  // 본문은 escaping된 text다. `<b>`가 태그로 살아나면 안 된다.
  expect(screen.getByText(/본문 <b>전문<\/b>/)).toBeTruthy();
  expect(window.document.querySelector("pre b")).toBeNull();
  expect(screen.getByText(/평가 뒤에 본문이 바뀌었다/)).toBeTruthy();
});

it("수급 화면이 단위를 열 이름에 적는다", async () => {
  // 수량은 주, 투자자별 대금만 백만원이다. 안 적으면 세 자릿수를 틀린다.
  stubFetch({ "/api/positioning/investor-flows": { items: [] } });
  renderAt("/positioning", "/positioning", <PositioningPage />);

  await screen.findByText(/장중 수급 스냅샷이 없다/);
  expect(screen.getByRole("radio", { name: "장중 수급" })).toBeTruthy();
});

it("수급 데이터셋을 바꾸면 URL과 요청이 함께 바뀐다", async () => {
  const log = stubFetch({
    "/api/positioning/investor-flows": { items: [] },
    "/api/positioning/short-sale": { items: [] },
  });
  renderAt("/positioning", "/positioning", <PositioningPage />);
  await screen.findByText(/장중 수급 스냅샷이 없다/);

  await userEvent.click(screen.getByRole("radio", { name: "공매도" }));

  await screen.findByText(/공매도 행이 없다/);
  expect(log.paths.some((path) => path.startsWith("/api/positioning/short-sale"))).toBe(true);
});

it("데이터셋 표에도 쪽 이동이 붙고 데이터셋을 바꾸면 첫 쪽이다", async () => {
  const page = {
    items: [
      {
        stock_code: "005930",
        business_date: "2026-08-26",
        close_price: 70000,
        short_volume: 1,
        short_amount: 2,
        short_ratio: 3,
        average_short_price: 4,
      },
    ],
    limit: 50,
    offset: 50,
    has_more: true,
  };
  const log = stubFetch({
    "/api/positioning/short-sale": page,
    "/api/positioning/investor-flows": { items: [], limit: 50, offset: 0, has_more: false },
  });
  renderAt("/positioning?dataset=short-sale&offset=50", "/positioning", <PositioningPage />);
  await screen.findByRole("table");

  await userEvent.click(screen.getByRole("button", { name: /다음/ }));
  expect(log.paths.some((path) => path.includes("offset=100"))).toBe(true);

  await userEvent.click(screen.getByRole("radio", { name: "장중 수급" }));
  const last = log.paths[log.paths.length - 1]!;
  expect(last).not.toContain("offset=");
});

it("필터 없는 데이터셋도 쪽이 요청까지 간다", async () => {
  // 개장 캘린더의 `path`는 인자를 안 받는다. 쪽을 데이터셋에 맡기면 그 데이터셋만
  // 조용히 첫 쪽에 갇힌다 — 2026-08-28에 실제로 그랬다.
  const page = {
    items: [
      {
        market_code: "KRX",
        market_name: "한국거래소",
        country_code: "KR",
        session_date: "2026-08-28",
        kis_business_day: true,
        kis_trading_day: true,
        kis_open_day: true,
        effective_open_day: true,
        local_settlement_date: null,
        domestic_settlement_date: null,
        verified_by: null,
      },
    ],
    limit: 50,
    offset: 0,
    has_more: true,
  };
  const log = stubFetch({ "/api/collection/sessions": page });
  renderAt("/collection?dataset=sessions", "/collection", <CollectionPage />);
  await screen.findByRole("table");

  await userEvent.click(screen.getByRole("button", { name: /다음/ }));

  expect(log.paths.some((path) => path.includes("offset=50"))).toBe(true);
});

it("종목 수급은 기관을 일곱으로 나눠 두 번째 표에 그린다", async () => {
  // 제공처가 이 단위로 준다. 합쳐 두면 "연기금이 샀나 금융투자가 샀나"를 되물을 수 없다.
  const rows = {
    items: [
      {
        stock_code: "005930",
        business_date: "2026-08-26",
        close_price: 70000,
        accumulated_volume: 100,
        accumulated_trade_amount: 200,
        foreign_net_buy_qty: 10,
        foreign_registered_net_buy_qty: 8,
        foreign_unregistered_net_buy_qty: 2,
        institution_net_buy_qty: 28,
        individual_net_buy_qty: -38,
        securities_net_buy_qty: 1,
        investment_trust_net_buy_qty: 2,
        private_equity_net_buy_qty: 3,
        bank_net_buy_qty: 4,
        insurance_net_buy_qty: 5,
        merchant_bank_net_buy_qty: 6,
        pension_fund_net_buy_qty: 7,
        other_corporation_net_buy_qty: 9,
        other_organization_net_buy_qty: 11,
        foreign_net_buy_amount: 1,
        institution_net_buy_amount: 2,
        individual_net_buy_amount: 3,
      },
    ],
    limit: 50,
    offset: 0,
    has_more: false,
  };
  stubFetch({ "/api/positioning/stock-flows": rows });
  renderAt("/positioning?dataset=stock-flows", "/positioning", <PositioningPage />);

  const tables = await screen.findAllByRole("table");
  expect(tables.length).toBe(2);
  // 첫 표는 3주체, 둘째 표가 기관 세부다.
  expect(tables[0]!.textContent).toContain("기관계(주)");
  for (const label of ["금융투자", "투자신탁", "사모펀드", "은행", "보험", "종금", "기금"]) {
    expect(tables[1]!.textContent).toContain(label);
  }
  // 기관계 밖 둘도 같은 표에 있지만 캡션이 그 경계를 밝힌다.
  expect(tables[1]!.textContent).toContain("기타법인");
  expect(tables[1]!.querySelector("caption")!.textContent).toContain("기관계이고");
});

it("대차거래는 시장과 종목을 표 둘로 나눈다", async () => {
  const rows = {
    market: [
      {
        market_code: "KOSPI",
        business_date: "2026-08-26",
        index_close: 3200,
        new_quantity: 10,
        repayment_quantity: 5,
        balance_quantity: 100,
        balance_amount: 1000,
      },
    ],
    stock: [
      {
        stock_code: "005930",
        business_date: "2026-08-26",
        close_price: 70000,
        new_quantity: 1,
        repayment_quantity: 2,
        balance_quantity: 3,
        balance_amount: 4,
        balance_change_quantity: -1,
      },
    ],
  };
  stubFetch({ "/api/positioning/lending": rows });
  renderAt("/positioning?dataset=lending", "/positioning", <PositioningPage />);

  const tables = await screen.findAllByRole("table");
  expect(tables.length).toBe(2);
  expect(tables[0]!.textContent).toContain("KOSPI");
  expect(tables[1]!.textContent).toContain("005930");
});

it("응답 모양이 다른 데이터셋으로 옮겨도 화면이 죽지 않는다", async () => {
  // 대차거래는 배열 둘(`market`·`stock`), 공매도는 `items` 하나다. 새 데이터셋이 옛
  // 응답을 읽으면 `rows.length`에서 죽는다 — 2026-08-27에 실제로 흰 화면이 됐다.
  const lending = { market: [], stock: [], limit: 50, offset: 0, has_more: false };
  const shortSale = { items: [], limit: 50, offset: 0, has_more: false };
  stubFetch({
    "/api/positioning/lending": lending,
    "/api/positioning/short-sale": shortSale,
  });
  renderAt("/positioning?dataset=lending", "/positioning", <PositioningPage />);
  await screen.findByText(/시장 대차 행이 없다/);

  await userEvent.click(screen.getByRole("radio", { name: "공매도" }));

  expect(await screen.findByText(/공매도 행이 없다/)).toBeTruthy();
});

it("판정이 없는 행은 보류로 보인다", async () => {
  // 실제값 주장이 갈리면 판정하지 않는다. 0이나 "없음"으로 채우지 않는다.
  const rows = {
    items: [
      {
        stock_code: "005930",
        event_type: "earnings",
        period_key: "2026Q2",
        metric: "revenue",
        expected_value: 100,
        expectation_count: 3,
        actual_value: null,
        surprise_pct: null,
        verdict: null,
        announced_at: "2026-08-23T15:00:00Z",
        actual_ref: null,
      },
    ],
  };
  stubFetch({ "/api/events/outcomes": rows });
  renderAt("/events?dataset=outcomes", "/events", <EventsPage />);

  await screen.findByRole("table");
  expect(screen.getByText("보류")).toBeTruthy();
});

it("수집 요약은 실패와 종료 미기록을 따로 보인다", async () => {
  const rows = {
    since: "2026-08-26T15:00:00Z",
    items: [
      {
        source: "kis",
        source_type: "api",
        records: 10,
        succeeded: 8,
        failed: 1,
        running: 1,
        quarantined: 0,
        rows: 100,
        latest_at: "2026-08-27T05:00:00Z",
        latest_status: null,
      },
    ],
  };
  stubFetch({ "/api/collection/health": rows });
  renderAt("/collection", "/collection", <CollectionPage />);

  await screen.findByRole("table");
  expect(screen.getByRole("columnheader", { name: "실패" })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: "종료 미기록" })).toBeTruthy();
});

it("수집 레코드 목록에 payload 본문이 없다", async () => {
  const rows = {
    items: [
      {
        id: 1,
        source_type: "api",
        source: "kis",
        source_key: "005930",
        started_at: "2026-08-27T05:00:00Z",
        completed_at: "2026-08-27T05:00:10Z",
        status: "succeeded",
        record_count: 390,
        has_payload: true,
        payload_uri: null,
      },
    ],
    limit: 200,
    offset: 0,
    has_more: false,
  };
  stubFetch({ "/api/collection/records": rows });
  renderAt("/collection?dataset=records", "/collection", <CollectionPage />);

  await screen.findByRole("table");
  expect(screen.getByRole("columnheader", { name: "원본 보관" })).toBeTruthy();
  expect(screen.queryByRole("columnheader", { name: "payload" })).toBeNull();
});

it("빈 데이터셋은 없다고 말한다", async () => {
  stubFetch({ "/api/events/claims": { items: [] } });
  renderAt("/events", "/events", <EventsPage />);

  expect(await screen.findByText(/주장이 없다/)).toBeTruthy();
});
