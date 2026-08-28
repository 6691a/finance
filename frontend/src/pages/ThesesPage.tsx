// 판단 목록. 12단계 목록 API를 그대로 쓰는 보조 탐색 경로다.
//
// **여기의 날짜 축은 `run_date`다** — 실행 목록의 `started_at`과 다르다. 판단은 어느
// 세션을 두고 한 것인지가 축이고, 실행은 언제 돌았는지가 축이다.

import { Link, useSearchParams } from "react-router-dom";

import { query, useJson } from "../api";
import { Async, Empty } from "../components/AsyncState";
import Pager, { pageOf } from "../components/Pager";
import { numberText, percentText, signedPercentText } from "../format";
import type { ThesisList } from "../types";

const SLOTS = [
  "pre_open",
  "intraday_morning",
  "intraday_midday",
  "intraday_afternoon",
  "pre_close",
  "post_close",
  "post_nxt_close",
];

export default function ThesesPage() {
  const [params, setParams] = useSearchParams();
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const slot = params.get("slot") ?? "";
  const subject = params.get("subject_code") ?? "";
  const offset = Number(params.get("offset") ?? 0);

  const resource = useJson<ThesisList>(
    `/api/theses${query({ from, to, slot, subject_code: subject, offset: offset || undefined })}`,
  );

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("offset");
    setParams(next);
  };

  const page = (value: number) => {
    const next = new URLSearchParams(params);
    if (value > 0) next.set("offset", String(value));
    else next.delete("offset");
    setParams(next);
  };

  return (
    <section>
      <h2>판단</h2>
      <div className="filters">
        <label>
          세션 날짜(부터, KST)
          <input type="date" value={from} onChange={(event) => set("from", event.target.value)} />
        </label>
        <label>
          세션 날짜(까지, KST)
          <input type="date" value={to} onChange={(event) => set("to", event.target.value)} />
        </label>
        <label>
          슬롯
          <select value={slot} onChange={(event) => set("slot", event.target.value)}>
            <option value="">전부</option>
            {SLOTS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          대상 코드
          <input
            type="text"
            value={subject}
            placeholder="KOSPI"
            onChange={(event) => set("subject_code", event.target.value)}
          />
        </label>
      </div>

      <Async resource={resource} what="판단 목록" back="/theses">
        {(data) =>
          data.items.length === 0 ? (
            <Empty>이 조건에 판단이 없다.</Empty>
          ) : (
            <>
              <table>
                <caption>
                  기준 시각 내림차순. **평가가 없으면 평균 Brier가 `—`이고 0이 아니다.**
                </caption>
                <thead>
                  <tr>
                    <th scope="col">세션일</th>
                    <th scope="col">슬롯</th>
                    <th scope="col">대상</th>
                    <th scope="col">상승</th>
                    <th scope="col">하락</th>
                    <th scope="col">횡보</th>
                    <th scope="col">상승 폭</th>
                    <th scope="col">하락 폭</th>
                    <th scope="col">채점</th>
                    <th scope="col">해설</th>
                    <th scope="col">평균 Brier</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((thesis) => (
                    <tr key={thesis.id}>
                      <td>{thesis.run_date}</td>
                      <td>{thesis.run_slot}</td>
                      <td>
                        <Link to={`/theses/${thesis.id}`}>
                          {thesis.label} ({thesis.subject_code})
                        </Link>
                      </td>
                      <td className="up">{percentText(thesis.prob_up)}</td>
                      <td className="down">{percentText(thesis.prob_down)}</td>
                      <td>{percentText(thesis.prob_flat)}</td>
                      <td>{signedPercentText(thesis.up_return_pct)}</td>
                      <td>
                        {thesis.down_return_pct === null
                          ? "—"
                          : `-${thesis.down_return_pct.toFixed(2)}%`}
                      </td>
                      <td>{thesis.graded_horizons}</td>
                      <td>{thesis.narrated_horizons}</td>
                      <td>{numberText(thesis.mean_brier)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pager page={pageOf(data)} onMove={page} />
            </>
          )
        }
      </Async>
    </section>
  );
}
