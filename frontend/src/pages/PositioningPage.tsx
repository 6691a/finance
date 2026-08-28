// 수급·신용·대차·공매도. 데이터셋 아홉을 탭으로 오간다.
//
// **단위를 열 이름에 적는다.** 수량은 주, 금액은 원인데 KIS의 투자자별 대금만 백만원이라,
// 적지 않으면 읽는 사람이 세 자릿수를 틀린다.
//
// **장중 스냅샷과 확정 일별값을 다른 데이터셋으로 둔다.** 앞은 분 단위 추정이고 뒤는
// 마감 뒤 확정이라 한 표에 놓으면 어느 쪽 숫자인지 못 가른다.

import DatasetBrowser, { type Dataset } from "../components/DatasetBrowser";
import type { Column } from "../components/DataTable";
import { integerText, kstText, numberText } from "../format";
import { type StockNames, stockText } from "../stocks";
import type {
  CreditBalanceRow,
  CreditRankingList,
  CreditRankingRow,
  InvestorEstimateRow,
  InvestorFlowPoint,
  Items,
  LendingList,
  MarketFundsRow,
  MarketMovementPoint,
  ShortSaleRow,
  StockFlowRow,
} from "../types";

function at<T>(key: string, label: string, pick: (row: T) => string | null): Column<T> {
  return {
    key,
    label,
    value: (row) => {
      const value = pick(row);
      return value === null ? "—" : <time dateTime={value}>{kstText(value)}</time>;
    },
  };
}

function num<T>(key: string, label: string, pick: (row: T) => number | null): Column<T> {
  return { key, label, value: (row) => integerText(pick(row)) };
}

function dec<T>(key: string, label: string, pick: (row: T) => number | null, digits = 2): Column<T> {
  return { key, label, value: (row) => numberText(pick(row), digits) };
}

function text<T>(key: string, label: string, pick: (row: T) => string | null): Column<T> {
  return { key, label, value: (row) => pick(row) ?? "—" };
}

const DATASETS: Dataset[] = [
  {
    id: "investor-flows",
    label: "장중 수급",
    note: "시장 전체의 장중 누적 매매동향. **분 단위 추정이고 확정값이 아니다.**",
    filters: ["dates", "market"],
    path: (params) => `/api/positioning/investor-flows?${params}`,
    tables: (data: Items<InvestorFlowPoint>, _names: StockNames) => [
      {
        caption: "관측 시각 오름차순 · 수량은 주, 대금은 백만원",
        empty: "이 구간에 장중 수급 스냅샷이 없다.",
        rows: data.items,
        columns: [
          text<InvestorFlowPoint>("market", "시장", (row) => row.market_code),
          at<InvestorFlowPoint>("at", "관측(KST)", (row) => row.observed_at),
          num<InvestorFlowPoint>("f", "외국인(주)", (row) => row.foreign_net_buy_qty),
          num<InvestorFlowPoint>("i", "기관(주)", (row) => row.institution_net_buy_qty),
          num<InvestorFlowPoint>("p", "개인(주)", (row) => row.individual_net_buy_qty),
          num<InvestorFlowPoint>("fa", "외국인(백만원)", (row) => row.foreign_net_buy_amount),
          num<InvestorFlowPoint>("ia", "기관(백만원)", (row) => row.institution_net_buy_amount),
          num<InvestorFlowPoint>("pension", "연기금(주)", (row) => row.pension_fund_net_buy_qty),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "market-movement",
    label: "시장 움직임",
    note: "상승·보합·하락 종목 수. 지수가 아니라 시장의 폭을 본다.",
    filters: ["dates"],
    path: (params) => `/api/positioning/market-movement?${params}`,
    tables: (data: Items<MarketMovementPoint>, _names: StockNames) => [
      {
        caption: "관측 시각 오름차순",
        empty: "이 구간에 시장 움직임 스냅샷이 없다.",
        rows: data.items,
        columns: [
          text<MarketMovementPoint>("s", "지수", (row) => row.symbol),
          at<MarketMovementPoint>("at", "관측(KST)", (row) => row.observed_at),
          num<MarketMovementPoint>("up", "상한", (row) => row.upper_limit_count),
          num<MarketMovementPoint>("r", "상승", (row) => row.rising_count),
          num<MarketMovementPoint>("u", "보합", (row) => row.unchanged_count),
          num<MarketMovementPoint>("f", "하락", (row) => row.falling_count),
          num<MarketMovementPoint>("low", "하한", (row) => row.lower_limit_count),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "stock-flows",
    label: "종목 수급(확정)",
    note: "장 마감 뒤 확정된 일별값. 가격도 같은 행에 있다 — **국내 종목 일봉의 원본이 여기다.**",
    filters: ["dates", "stock"],
    path: (params) => `/api/positioning/stock-flows?${params}`,
    tables: (data: Items<StockFlowRow>, names: StockNames) => [
      {
        caption: "거래일 오름차순 · 3주체(주) · 거래대금은 원, 투자자별 대금은 백만원",
        empty: "이 구간에 확정 수급이 없다.",
        rows: data.items,
        columns: [
          {
            key: "c",
            label: "종목",
            value: (row: StockFlowRow) => stockText(row.stock_code, names),
          },
          text<StockFlowRow>("d", "거래일", (row) => row.business_date),
          dec<StockFlowRow>("close", "종가(원)", (row) => row.close_price, 0),
          num<StockFlowRow>("v", "거래량(주)", (row) => row.accumulated_volume),
          num<StockFlowRow>("f", "외국인(주)", (row) => row.foreign_net_buy_qty),
          num<StockFlowRow>("fr", "└ 등록(주)", (row) => row.foreign_registered_net_buy_qty),
          num<StockFlowRow>("fn", "└ 미등록(주)", (row) => row.foreign_unregistered_net_buy_qty),
          num<StockFlowRow>("i", "기관계(주)", (row) => row.institution_net_buy_qty),
          num<StockFlowRow>("p", "개인(주)", (row) => row.individual_net_buy_qty),
          num<StockFlowRow>("fa", "외국인(백만원)", (row) => row.foreign_net_buy_amount),
          num<StockFlowRow>("ia", "기관계(백만원)", (row) => row.institution_net_buy_amount),
          num<StockFlowRow>("pa", "개인(백만원)", (row) => row.individual_net_buy_amount),
        ] as Column<never>[],
      },
      {
        // **표를 나눈다.** 한 표에 스무 칸을 넣으면 가로로 밀려 어느 칸이 어느 주체인지
        // 읽을 수 없다. 행은 같고(종목·거래일) 보는 각도만 다르다.
        caption: "같은 행의 기관 세부 · 앞의 일곱이 기관계이고 기타법인·기타단체는 그 밖이다(주)",
        empty: "이 구간에 확정 수급이 없다.",
        rows: data.items,
        columns: [
          {
            key: "c2",
            label: "종목",
            value: (row: StockFlowRow) => stockText(row.stock_code, names),
          },
          text<StockFlowRow>("d2", "거래일", (row) => row.business_date),
          num<StockFlowRow>("sec", "금융투자", (row) => row.securities_net_buy_qty),
          num<StockFlowRow>("itr", "투자신탁", (row) => row.investment_trust_net_buy_qty),
          num<StockFlowRow>("pef", "사모펀드", (row) => row.private_equity_net_buy_qty),
          num<StockFlowRow>("bnk", "은행", (row) => row.bank_net_buy_qty),
          num<StockFlowRow>("ins", "보험", (row) => row.insurance_net_buy_qty),
          num<StockFlowRow>("mrb", "종금", (row) => row.merchant_bank_net_buy_qty),
          num<StockFlowRow>("pen", "기금", (row) => row.pension_fund_net_buy_qty),
          num<StockFlowRow>("etc", "기타법인", (row) => row.other_corporation_net_buy_qty),
          num<StockFlowRow>("eto", "기타단체", (row) => row.other_organization_net_buy_qty),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "estimates",
    label: "종목 수급(추정)",
    note: "장중 슬롯마다 갱신되는 **추정**이다. 같은 거래일에 여러 행이 온다.",
    filters: ["dates", "stock"],
    path: (params) => `/api/positioning/estimates?${params}`,
    tables: (data: Items<InvestorEstimateRow>, names: StockNames) => [
      {
        caption: "거래일·수집 시각 오름차순",
        empty: "이 구간에 추정 수급이 없다.",
        rows: data.items,
        columns: [
          { key: "c", label: "종목", value: (row: InvestorEstimateRow) => stockText(row.stock_code, names) },
          text<InvestorEstimateRow>("d", "거래일", (row) => row.business_date),
          text<InvestorEstimateRow>("slot", "슬롯", (row) => row.source_time_code),
          num<InvestorEstimateRow>("f", "외국인(주)", (row) => row.foreign_net_buy_qty),
          num<InvestorEstimateRow>("i", "기관(주)", (row) => row.institution_net_buy_qty),
          num<InvestorEstimateRow>("t", "합계(주)", (row) => row.total_net_buy_qty),
          at<InvestorEstimateRow>("at", "수집(KST)", (row) => row.collected_at),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "short-sale",
    label: "공매도",
    note: "비중은 제공처가 준 값 그대로다 — 우리가 다시 계산하지 않는다.",
    filters: ["dates", "stock"],
    path: (params) => `/api/positioning/short-sale?${params}`,
    tables: (data: Items<ShortSaleRow>, names: StockNames) => [
      {
        caption: "영업일 오름차순 · 금액은 원",
        empty: "이 구간에 공매도 행이 없다.",
        rows: data.items,
        columns: [
          { key: "c", label: "종목", value: (row: ShortSaleRow) => stockText(row.stock_code, names) },
          text<ShortSaleRow>("d", "영업일", (row) => row.business_date),
          dec<ShortSaleRow>("close", "종가(원)", (row) => row.close_price, 0),
          num<ShortSaleRow>("q", "공매도(주)", (row) => row.short_sale_quantity),
          dec<ShortSaleRow>("vr", "거래량 비중(%)", (row) => row.short_sale_volume_ratio),
          num<ShortSaleRow>("a", "공매도 대금(원)", (row) => row.short_sale_amount),
          dec<ShortSaleRow>("ar", "대금 비중(%)", (row) => row.short_sale_amount_ratio),
          dec<ShortSaleRow>("avg", "평균가(원)", (row) => row.short_sale_average_price, 0),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "lending",
    label: "대차거래",
    note: "**시장과 종목을 표 둘로 나눈다** — 축은 같지만 단위와 뜻이 다르다.",
    filters: ["dates", "stock", "market"],
    path: (params) => `/api/positioning/lending?${params}`,
    tables: (data: LendingList, names: StockNames) => [
      {
        caption: "시장 전체 · 영업일 오름차순",
        empty: "이 구간에 시장 대차 행이 없다.",
        rows: data.market,
        columns: [
          text("m", "시장", (row: { market_code: string }) => row.market_code),
          text("d", "영업일", (row: { business_date: string }) => row.business_date),
          num("n", "신규(주)", (row: { new_quantity: number | null }) => row.new_quantity),
          num("r", "상환(주)", (row: { repayment_quantity: number | null }) => row.repayment_quantity),
          num("b", "잔고(주)", (row: { balance_quantity: number | null }) => row.balance_quantity),
          num("ba", "잔고(원)", (row: { balance_amount: number | null }) => row.balance_amount),
        ] as Column<never>[],
      },
      {
        caption: "종목별 · 영업일 오름차순",
        empty: "이 구간에 종목 대차 행이 없다.",
        rows: data.stock,
        columns: [
          { key: "c", label: "종목", value: (row: { stock_code: string }) => stockText(row.stock_code, names) },
          text("d", "영업일", (row: { business_date: string }) => row.business_date),
          num("n", "신규(주)", (row: { new_quantity: number | null }) => row.new_quantity),
          num("r", "상환(주)", (row: { repayment_quantity: number | null }) => row.repayment_quantity),
          num("b", "잔고(주)", (row: { balance_quantity: number | null }) => row.balance_quantity),
          num("ba", "잔고(원)", (row: { balance_amount: number | null }) => row.balance_amount),
          num("ch", "잔고 증감(주)", (row: { balance_change_quantity: number | null }) => row.balance_change_quantity),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "credit",
    label: "신용잔고",
    note: "융자(loan)와 신용대주(short_loan)가 따로 있다. **축이 거래일이고 결제일은 별도 칸이다.**",
    filters: ["dates", "stock"],
    path: (params) => `/api/positioning/credit?${params}`,
    tables: (data: Items<CreditBalanceRow>, names: StockNames) => [
      {
        caption: "거래일 오름차순 · 금액은 원",
        empty: "이 구간에 신용잔고 행이 없다.",
        rows: data.items,
        columns: [
          { key: "c", label: "종목", value: (row: CreditBalanceRow) => stockText(row.stock_code, names) },
          text<CreditBalanceRow>("d", "거래일", (row) => row.trade_date),
          text<CreditBalanceRow>("s", "결제일", (row) => row.settlement_date),
          num<CreditBalanceRow>("lq", "융자 잔고(주)", (row) => row.loan_balance_quantity),
          num<CreditBalanceRow>("la", "융자 잔고(원)", (row) => row.loan_balance_amount),
          dec<CreditBalanceRow>("lr", "융자 비율(%)", (row) => row.loan_balance_rate),
          num<CreditBalanceRow>("sq", "대주 잔고(주)", (row) => row.short_loan_balance_quantity),
          dec<CreditBalanceRow>("sr", "대주 비율(%)", (row) => row.short_loan_balance_rate),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "funds",
    label: "증시자금",
    note: "고객예탁금·신용융자·펀드가 한 행이다. 단위는 억원이다.",
    filters: ["dates"],
    path: (params) => `/api/positioning/funds?${params}`,
    tables: (data: Items<MarketFundsRow>, _names: StockNames) => [
      {
        caption: "영업일 오름차순 · 금액은 억원",
        empty: "이 구간에 증시자금 행이 없다.",
        rows: data.items,
        columns: [
          text<MarketFundsRow>("d", "영업일", (row) => row.business_date),
          dec<MarketFundsRow>("i", "지수", (row) => row.index_close),
          num<MarketFundsRow>("dep", "고객예탁금", (row) => row.customer_deposit),
          num<MarketFundsRow>("chg", "예탁금 증감", (row) => row.customer_deposit_change),
          num<MarketFundsRow>("cr", "신용융자", (row) => row.credit_loan_balance),
          num<MarketFundsRow>("un", "미수금", (row) => row.unsettled_amount),
          num<MarketFundsRow>("eq", "주식형 펀드", (row) => row.equity_fund_amount),
          num<MarketFundsRow>("mmf", "MMF", (row) => row.mmf_amount),
        ] as Column<never>[],
      },
    ],
  },
  {
    id: "credit-ranking",
    label: "융자잔고 순위",
    note: "**하루를 고른다** — 날짜마다 순위가 다시 매겨져 구간으로 주면 같은 종목이 여러 번 나온다.",
    filters: ["day"],
    path: (params) => `/api/positioning/credit-ranking?${params}`,
    choices: (data: CreditRankingList) => ({ name: "standard_date", values: data.standard_dates }),
    tables: (data: CreditRankingList, _names: StockNames) => [
      {
        caption: "순위 오름차순 · 금액은 원",
        empty: "이 기준일에 순위 행이 없다.",
        rows: data.items,
        columns: [
          num<CreditRankingRow>("rank", "순위", (row) => row.rank),
          // **제공처가 이름을 함께 준다.** 우리 마스터에 없는 종목이 순위에 들어오므로
          // 그 이름을 그대로 쓴다 — 마스터로 덮으면 목록 밖 종목이 코드로만 남는다.
          {
            key: "c",
            label: "종목",
            value: (row: CreditRankingRow) =>
              row.stock_name === null ? row.stock_code : `${row.stock_name}(${row.stock_code})`,
          },
          dec<CreditRankingRow>("close", "종가(원)", (row) => row.close_price, 0),
          num<CreditRankingRow>("q", "융자 잔고(주)", (row) => row.loan_balance_quantity),
          num<CreditRankingRow>("a", "융자 잔고(원)", (row) => row.loan_balance_amount),
          dec<CreditRankingRow>("r", "비율(%)", (row) => row.loan_balance_rate),
          dec<CreditRankingRow>("g", "증감률(%)", (row) => row.loan_balance_growth_rate),
        ] as Column<never>[],
      },
    ],
  },
] as Dataset[];

export default function PositioningPage() {
  return (
    <DatasetBrowser
      title="수급"
      note="투자자 매매동향과 신용·대차·공매도. 단위가 칸마다 달라 열 이름에 함께 적는다."
      datasets={DATASETS}
      defaultDays={30}
    />
  );
}
