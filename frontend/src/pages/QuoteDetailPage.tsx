// 심볼 하나의 봉. 이 판의 본문이다.
//
// **거래소를 반드시 고르게 한다.** 같은 종목이 KRX와 NXT에서 따로 체결되므로 통합 선을
// 만들지 않는다 — 둘을 겹쳐 보는 것과 합치는 것은 다르다.
//
// **상한을 넘는 조합은 미리 막는다.** 400을 화면에서 보는 일이 정상 흐름이 되면 안 된다.
// 프리셋마다 예상 점 수를 계산해 넘는 것을 고르지 못하게 한다.

import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { barChartData, dailyChartData, labelAt, lastPoint } from "../chart";
import { Async, Empty } from "../components/AsyncState";
import ChartView from "../components/ChartView";
import { integerText, kstText, numberText } from "../format";
import { stockText, useStockNames } from "../stocks";
import type { BarSeries, DailySeries } from "../types";
import { kindName } from "./QuotesPage";

const EXCHANGES = ["KRX", "NXT", "NYSE", "NASDAQ"];

// 프리셋과 그 길이(분). `1d` 이상은 일봉으로 간다.
const PRESETS: { id: string; label: string; minutes: number }[] = [
  { id: "1d", label: "오늘", minutes: 60 * 24 },
  { id: "5d", label: "5일", minutes: 60 * 24 * 5 },
  { id: "1mo", label: "1개월", minutes: 60 * 24 * 30 },
];

const INTERVALS: { id: string; label: string; minutes: number }[] = [
  { id: "1m", label: "1분", minutes: 1 },
  { id: "5m", label: "5분", minutes: 5 },
  { id: "15m", label: "15분", minutes: 15 },
  { id: "1h", label: "1시간", minutes: 60 },
];

const DAILY_RANGES = [
  { id: "1y", label: "1년", days: 365 },
  { id: "3y", label: "3년", days: 365 * 3 },
  { id: "all", label: "전체", days: 365 * 12 },
];

// 서버의 상한과 같은 값. 여기가 어긋나면 화면이 막지 못한 요청이 400으로 돌아온다.
export const MAX_POINTS = 5000;

/** 프리셋 × 간격의 **최악** 점 수. 밤에 거래가 없어 실제로는 이보다 적다. */
export function estimatePoints(rangeMinutes: number, intervalMinutes: number): number {
  return Math.ceil(rangeMinutes / intervalMinutes);
}

function isoMinutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString().replace(/\.\d+Z$/, "Z");
}

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

/**
 * 이 응답이 분봉인가. **축 이름이 곧 판별자다** — 분봉은 `times`(UTC 시각), 일봉은
 * `dates`(그 시장의 거래일)이고 둘은 절대 함께 오지 않는다.
 */
export function isBar(series: BarSeries | DailySeries): series is BarSeries {
  return "times" in series;
}

export default function QuoteDetailPage() {
  const { kind = "", symbol = "" } = useParams();
  const [params, setParams] = useSearchParams();
  // 종목은 코드가 제목이 되면 읽히지 않는다. 매크로 심볼(KOSPI 등)은 그 자체가 이름이다.
  const names = useStockNames();
  const exchange = params.get("exchange") ?? "";
  const mode = params.get("mode") === "daily" ? "daily" : "bar";
  const preset = params.get("range") ?? "1d";
  const interval = params.get("interval") ?? "5m";
  const dailyRange = params.get("daily_range") ?? "1y";

  const range = PRESETS.find((entry) => entry.id === preset) ?? PRESETS[0]!;
  const step = INTERVALS.find((entry) => entry.id === interval) ?? INTERVALS[1]!;
  const days = DAILY_RANGES.find((entry) => entry.id === dailyRange) ?? DAILY_RANGES[0]!;

  // **거래소를 고르기 전에는 부르지 않는다.** 서버가 422로 답할 요청을 보낼 이유가 없다.
  const needsExchange = kind === "equity" && !exchange;
  const path = needsExchange
    ? null
    : mode === "daily"
      ? `/api/quotes/daily${query({ kind, symbol, exchange, from: daysAgo(days.days) })}`
      : `/api/quotes/bars${query({
          kind,
          symbol,
          exchange,
          interval: step.id,
          from: isoMinutesAgo(range.minutes),
        })}`;

  const resource = useJson<BarSeries | DailySeries>(path);

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };

  // **`mode`가 아니라 응답의 모양으로 가른다.** URL이 먼저 바뀌고 응답은 그다음에 오므로,
  // 그 한 렌더 동안 새 mode와 옛 응답이 만난다. mode를 믿고 `as DailySeries`로 단정하면
  // 그때 `dates`가 undefined라 화면이 통째로 죽는다(2026-08-27에 실제로 그랬다).
  const chart = useMemo(() => {
    const data = resource.data;
    if (data === null) return null;
    return isBar(data) ? barChartData(data) : dailyChartData(data);
  }, [resource.data]);

  return (
    <section>
      <h2>
        {kind === "equity" ? stockText(symbol, names) : symbol} · {kindName(kind)}
        {exchange ? ` · ${exchange}` : ""}
      </h2>
      <nav className="pager" aria-label="관련 화면">
        <Link to="/quotes">심볼 목록으로</Link>
      </nav>

      <div className="filters">
        <label>
          축
          <select value={mode} onChange={(event) => set("mode", event.target.value)}>
            <option value="bar">분봉</option>
            <option value="daily">일봉</option>
          </select>
        </label>
        {kind === "equity" && (
          <label>
            거래소
            <select value={exchange} onChange={(event) => set("exchange", event.target.value)}>
              <option value="">고르세요</option>
              {EXCHANGES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        )}
        {mode === "bar" ? (
          <>
            <label>
              기간
              <select value={range.id} onChange={(event) => set("range", event.target.value)}>
                {PRESETS.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              간격
              <select value={step.id} onChange={(event) => set("interval", event.target.value)}>
                {INTERVALS.map((entry) => (
                  <option
                    key={entry.id}
                    value={entry.id}
                    // 상한을 넘는 조합은 아예 고르지 못하게 한다.
                    disabled={estimatePoints(range.minutes, entry.minutes) > MAX_POINTS}
                  >
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : (
          <label>
            기간
            <select value={days.id} onChange={(event) => set("daily_range", event.target.value)}>
              {DAILY_RANGES.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {needsExchange ? (
        <p className="warn">
          종목은 거래소를 골라야 한다. 같은 종목이 KRX와 NXT에서 따로 체결되므로 통합 시세를
          만들지 않는다 — 둘을 견주려면 각각 열어 본다.
        </p>
      ) : (
        <Async resource={resource} what="시세" back="/quotes">
          {(series) => {
            // 화면의 모양도 `mode`가 아니라 **응답**이 정한다. 컨트롤은 다음에 무엇을
            // 부를지를 정할 뿐이고, 지금 그려야 할 것은 지금 손에 있는 응답이다.
            const bars = isBar(series);
            const axis = bars ? series.times : series.dates;
            // 월물 배열은 지수선물 일봉에만 온다. 빈 배열이면 "월물 개념이 없다"다.
            const contracts = !bars && series.contracts.length > 0;
            // 확정 배열은 종목 분봉에만 온다. **false는 그 봉이 아직 바뀔 수 있다는 뜻이다** —
            // WebSocket 잠정 봉이 먼저 들어오고 REST가 나중에 덮는다.
            const settled = bars && series.settled.length > 0;
            const pending = settled ? series.settled.filter((value) => !value).length : 0;
            const last = lastPoint(axis, series.close);
            return series.points === 0 ? (
              <Empty>이 구간에 봉이 없다. 기간을 넓히거나 다른 거래소를 골라 본다.</Empty>
            ) : (
              <>
                <div className="readout">
                  <span className="value">{last === null ? "—" : numberText(last.value, 2)}</span>
                  <span>
                    기준{" "}
                    {last === null ? "—" : bars ? kstText(last.at) : last.at}{" "}
                    · {series.provider}
                    {series.exchange ? ` · ${series.exchange}` : ""}
                    {bars ? ` · ${series.interval}` : " · 일봉"}
                  </span>
                  <span>{integerText(series.points)}점</span>
                </div>

                {chart !== null && (
                  // **x축은 시각이 아니라 순번이다.** 장 마감과 다음 개장 사이의 빈
                  // 시간을 차트가 그리지 않게 하는 것이고, 라벨이 날짜를 함께 적어
                  // 어디서 하루가 넘어갔는지 읽힌다.
                  <ChartView
                    data={chart.data}
                    series={[
                      { label: "종가" },
                      { label: "거래량", secondary: true, bars: true },
                    ]}
                    time={false}
                    xLabel={(value) => labelAt(chart.labels, value)}
                    height={380}
                  />
                )}

                <h3>값</h3>
                <p className="state">
                  차트만으로는 정확한 숫자를 못 읽는다. 최근 200행만 보이고 나머지는 기간을
                  좁혀 읽는다.
                  {pending > 0 && (
                    <>
                      {" "}
                      **이 구간에 잠정 봉이 {pending}개 있다** — WebSocket이 먼저 쓴 값이고
                      REST 확정이 아직 안 덮었다. 고가·저가가 바뀔 수 있다.
                    </>
                  )}
                </p>
                <table>
                  <caption>
                    {bars ? "봉 시작 시각(KST)" : "거래일"} 내림차순. 원본 시각은 UTC다.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">{bars ? "시각" : "거래일"}</th>
                      <th scope="col">시가</th>
                      <th scope="col">고가</th>
                      <th scope="col">저가</th>
                      <th scope="col">종가</th>
                      <th scope="col">거래량</th>
                      {/* 지수선물만 월물이 온다. 갭이 급변인지 롤오버인지 이 칸이 가른다. */}
                      {contracts && <th scope="col">월물</th>}
                      {settled && <th scope="col">확정</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {axis
                      .map((at, index) => ({ at, index }))
                      .reverse()
                      .slice(0, 200)
                      .map(({ at, index }) => (
                        <tr key={at}>
                          <td>{bars ? <time dateTime={at}>{kstText(at)}</time> : at}</td>
                          <td>{numberText(series.open[index], 2)}</td>
                          <td>{numberText(series.high[index], 2)}</td>
                          <td>{numberText(series.low[index], 2)}</td>
                          <td>{numberText(series.close[index], 2)}</td>
                          <td>{integerText(series.volume[index])}</td>
                          {contracts && <td>{series.contracts[index] ?? "—"}</td>}
                          {settled && <td>{series.settled[index] ? "확정" : "잠정"}</td>}
                        </tr>
                      ))}
                  </tbody>
                </table>
              </>
            );
          }}
        </Async>
      )}
    </section>
  );
}
