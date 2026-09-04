// 하루의 전망 셋. **첫 화면이 여기다.**
//
// 추론이 코스피 하나로 좁아졌으니 "오늘 무엇이라고 말했나"가 곧 첫 질문이다.
//
// **슬롯 셋을 세로로 둔다.** 슬롯끼리 비교하는 것이 이 기능의 핵심이라 — 장전이 틀렸다는
// 것을 장중이 인정했는지가 한 화면에서 보여야 한다. 표를 날짜 축으로 길게 늘이면 그
// 비교가 사라진다.

import { Link, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import { bandText, kstTimeText, numberText, signedPercentText } from "../format";
import { FORECAST_SLOTS, labelOf } from "../labels";
import type { ForecastAccuracy, ForecastItem, ForecastList } from "../types";

/** 오늘(KST). 서버가 기본 창을 정하지만 화면은 "오늘"을 스스로 알아야 날짜 이동을 만든다. */
export function kstToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(now);
}

/** 하루를 더하거나 뺀 날짜. `Date` 산술을 UTC로 하고 문자열만 다룬다. */
export function shiftDay(day: string, days: number): string {
  const moment = new Date(`${day}T00:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() + days);
  return moment.toISOString().slice(0, 10);
}

/** 채점 결과 한 줄. **채점 전은 "채점 대기"이지 빈 칸이 아니다.** */
export function gradeText(item: ForecastItem): string {
  if (item.graded_at === null) return "채점 대기";
  const hit = item.hit ? "방향 ○" : "방향 ✗";
  const band = item.within_band ? "밴드 안" : "밴드 밖";
  return `실제 ${signedPercentText(item.actual_change_pct)} · ${hit} · ${band}`;
}

function Slot({ item }: { item: ForecastItem }) {
  const rising = item.direction === "up";
  return (
    <li className="panel">
      <h3>
        <Link to={`/forecast/${item.run_date}/${item.slot}`}>
          {kstTimeText(item.as_of_at)} {labelOf(FORECAST_SLOTS, item.slot)}
        </Link>
        {item.weak ? <span className="warn"> ⚠ 근거 없음</span> : null}
      </h3>
      <p className={rising ? "up" : "down"}>
        {rising ? "▲" : "▼"} {bandText(item.expected_change_pct, item.band_pct)}
      </p>
      <dl className="facts">
        <dt>기준</dt>
        <dd>
          {numberText(item.base_price, 2)}{" "}
          <span className="state">({item.slot === "pre_open" ? "전일 종가" : "현재가"})</span>
        </dd>
        <dt>여기까지</dt>
        <dd>{item.so_far_pct === null ? "—" : signedPercentText(item.so_far_pct)}</dd>
        <dt>이유</dt>
        <dd>
          {item.reason_count}건
          {item.rejected_reasons > 0 ? ` (버림 ${item.rejected_reasons})` : ""}
        </dd>
        <dt>채점</dt>
        <dd>{gradeText(item)}</dd>
      </dl>
    </li>
  );
}

export default function ForecastPage() {
  const [params, setParams] = useSearchParams();
  const day = params.get("day") ?? kstToday();

  const resource = useJson<ForecastList>(`/api/forecasts${query({ from: day, to: day })}`);
  const accuracy = useJson<ForecastAccuracy>("/api/forecasts/accuracy");

  const move = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("day", value);
    setParams(next);
  };

  const total = accuracy.data?.rows.find((row) => row.slot === "all") ?? null;

  return (
    <section>
      <h2>코스피 일일 전망</h2>
      <div className="filters">
        <button type="button" onClick={() => move(shiftDay(day, -1))}>
          ← 전날
        </button>
        <label>
          세션 날짜(KST)
          <input type="date" value={day} onChange={(event) => move(event.target.value)} />
        </label>
        <button type="button" onClick={() => move(shiftDay(day, 1))}>
          다음날 →
        </button>
        <button type="button" onClick={() => move(kstToday())}>
          오늘
        </button>
      </div>

      {/* **표본 수를 비율과 함께 보인다.** 3건에서 나온 67%가 100건처럼 읽히면 안 된다. */}
      <p className="state">
        {total === null
          ? "적중률을 불러오는 중이다."
          : `최근 채점 ${total.graded}건 · 방향 ${
              total.hit_rate === null ? "—" : `${(total.hit_rate * 100).toFixed(0)}%`
            } · 밴드 ${
              total.band_rate === null ? "—" : `${(total.band_rate * 100).toFixed(0)}%`
            } · 평균 오차 ${numberText(total.mean_abs_error, 2)}%p`}{" "}
        <Link to="/quality">품질 표로</Link>
      </p>

      <Async resource={resource} what="전망" back="/forecast">
        {(data) =>
          data.items.length === 0 ? (
            <Empty>그 날짜에는 전망이 없다.</Empty>
          ) : (
            <ul className="cards">
              {data.items.map((item) => (
                <Slot key={item.slot} item={item} />
              ))}
            </ul>
          )
        }
      </Async>
    </section>
  );
}
