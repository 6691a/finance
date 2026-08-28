// 판단 상세.
//
// **정보의 의미를 섞지 않는다.** 입력 상태는 최초 프롬프트에 준 것, 인용 근거는 모델의
// 최종 응답이 명시적으로 고른 것, 판단 이유는 모델이 최종 응답으로 설명한 것이다.
// 툴 결과에 있었다는 사실만으로 그 값이 결론을 **바꿨다**고 쓰지 않는다 — 그런 인과는
// 같은 입력에서 그 툴만 가린 ablation 실행을 비교해야 말할 수 있다.
//
// 화면에 보이는 이유는 모델의 **명시적** 판단 근거다. 숨은 chain-of-thought를 복원하지 않는다.

import { Link, useParams } from "react-router-dom";

import { useJson } from "../api";
import { Async } from "../components/AsyncState";
import { jsonText, kstText, numberText, percentText, safeHref, signedPercentText } from "../format";
import type { EvidenceCitation, ThesisDetail } from "../types";

function EvidenceTable({ rows, caption }: { rows: EvidenceCitation[]; caption: string }) {
  if (rows.length === 0) return <p className="state">{caption}: 없다.</p>;
  return (
    <table>
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">순위</th>
          <th scope="col">종류</th>
          <th scope="col">방향</th>
          <th scope="col" className="wrap">
            제목
          </th>
          <th scope="col" className="wrap">
            경로
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const href = safeHref(row.url);
          return (
            <tr key={`${row.kind}:${row.ref}:${row.rank}`}>
              <td>{row.rank}</td>
              <td>{row.kind}</td>
              <td>{row.direction ?? "—"}</td>
              <td className="wrap">
                {href === null ? (
                  // `http:`·`https:`가 아니면 링크로 만들지 않는다. 원문은 text로 남긴다.
                  <>
                    {row.title}
                    {row.url === null ? "" : ` (${row.url})`}
                  </>
                ) : (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {row.title}
                  </a>
                )}
              </td>
              <td className="wrap">{row.mechanism ?? "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function ThesisDetailPage() {
  const { thesisId } = useParams();
  const resource = useJson<ThesisDetail>(`/api/theses/${thesisId}`);

  return (
    <Async resource={resource} what="판단" back="/theses">
      {(thesis) => (
        <section>
          <h2>
            {thesis.label} ({thesis.subject_code}) · {thesis.run_date} {thesis.run_slot}
          </h2>
          <nav className="pager" aria-label="관련 화면">
            <Link to={`/theses/${thesis.id}/graph`}>관계 그래프</Link>
            {thesis.llm_run === null ? null : <Link to={`/runs/${thesis.llm_run.id}`}>이 판단을 만든 실행</Link>}
          </nav>

          <table>
            <caption>
              세 방향 확률과 **그 방향이라는 조건에서의** 등락률. 확률을 곱한 기대값이 아니다.
            </caption>
            <thead>
              <tr>
                <th scope="col">방향</th>
                <th scope="col">확률</th>
                <th scope="col">조건부 등락률</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">상승</th>
                <td>{percentText(thesis.prob_up)}</td>
                <td>{signedPercentText(thesis.up_return_pct)}</td>
              </tr>
              <tr>
                <th scope="row">하락</th>
                <td>{percentText(thesis.prob_down)}</td>
                <td>{thesis.down_return_pct === null ? "—" : `-${thesis.down_return_pct.toFixed(2)}%`}</td>
              </tr>
              <tr>
                <th scope="row">횡보</th>
                <td>{percentText(thesis.prob_flat)}</td>
                <td>±0.3% 안</td>
              </tr>
            </tbody>
          </table>

          <h3>명시적 판단 이유</h3>
          <div className="panel">
            <dl className="meta">
              <dt>오를 이유</dt>
              <dd className="wrap">{thesis.up_reasoning}</dd>
              <dt>내릴 이유</dt>
              <dd className="wrap">{thesis.down_reasoning}</dd>
              <dt>횡보할 이유</dt>
              <dd className="wrap">{thesis.flat_reasoning}</dd>
            </dl>
          </div>

          <h3>인용 근거</h3>
          <EvidenceTable rows={thesis.evidence} caption="원 판단이 인용한 근거(모델 최종 응답이 고른 것)" />

          <h3>당시 입력 상태</h3>
          <p className="state">모델의 최초 프롬프트에 제공된 스냅샷이다.</p>
          <pre>
            <code>{jsonText(thesis.input_state)}</code>
          </pre>

          <h3>실행</h3>
          {thesis.llm_run === null ? (
            <p className="state">실행 원장 도입 전 기록이다.</p>
          ) : (
            <div className="panel">
              <dl className="meta">
                <dt>실행</dt>
                <dd>
                  <Link to={`/runs/${thesis.llm_run.id}`}>
                    {thesis.llm_run.id} · {thesis.llm_run.kind} · {thesis.llm_run.status}
                  </Link>
                </dd>
                <dt>모델 · 판</dt>
                <dd>
                  {thesis.llm_model} · {thesis.prompt_version}
                </dd>
                <dt>왕복 · 툴</dt>
                <dd>
                  {thesis.llm_run.tool_rounds} · {thesis.llm_run.tool_call_count}
                </dd>
              </dl>
            </div>
          )}

          <h3>지평별 평가</h3>
          {thesis.outcomes.length === 0 ? (
            <p className="state">아직 채점·해설이 없다.</p>
          ) : (
            <table>
              <caption>
                Brier(방향), 크기 오차(폭), 판정(이유)은 **서로 다른 것을 잰다.** 합친 종합
                점수를 만들지 않는다.
              </caption>
              <thead>
                <tr>
                  <th scope="col">지평</th>
                  <th scope="col">실제 등락률</th>
                  <th scope="col">실제 방향</th>
                  <th scope="col">Brier</th>
                  <th scope="col">예측 크기</th>
                  <th scope="col">크기 오차</th>
                  <th scope="col">판정</th>
                  <th scope="col">기준(KST)</th>
                </tr>
              </thead>
              <tbody>
                {thesis.outcomes.map((outcome) => (
                  <tr key={outcome.horizon_days}>
                    <th scope="row">T+{outcome.horizon_days}</th>
                    <td>{signedPercentText(outcome.actual_return_pct)}</td>
                    <td>{outcome.actual_outcome ?? "—"}</td>
                    <td>{numberText(outcome.brier_score)}</td>
                    <td>{outcome.predicted_return_pct === null ? "—" : `${outcome.predicted_return_pct}%`}</td>
                    <td>{outcome.return_error_pct === null ? "—" : `${outcome.return_error_pct}%p`}</td>
                    <td>{outcome.verdict ?? "—"}</td>
                    <td>
                      <time dateTime={outcome.as_of_at}>{kstText(outcome.as_of_at)}</time>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {thesis.outcomes
            .filter((outcome) => outcome.narrative !== null)
            .map((outcome) => (
              <div key={outcome.horizon_days}>
                <h3>T+{outcome.horizon_days} 사후 해설</h3>
                <div className="panel">
                  <p className="wrap">{outcome.narrative}</p>
                  <dl className="meta">
                    <dt>판정</dt>
                    <dd>{outcome.verdict ?? "—"}</dd>
                    <dt>모델 · 판</dt>
                    <dd>
                      {outcome.llm_model ?? "—"} · {outcome.prompt_version ?? "—"}
                    </dd>
                    {outcome.narration_run === null ? null : (
                      <>
                        <dt>실행</dt>
                        <dd>
                          <Link to={`/runs/${outcome.narration_run.id}`}>{outcome.narration_run.id}</Link>
                        </dd>
                      </>
                    )}
                  </dl>
                </div>
                <EvidenceTable rows={outcome.evidence} caption={`T+${outcome.horizon_days} 해설이 인용한 근거`} />
              </div>
            ))}

          <h3>프롬프트에서 본 과거 판단</h3>
          {thesis.precedents.length === 0 ? (
            <p className="state">없다.</p>
          ) : (
            <ul>
              {thesis.precedents.map((precedent) => (
                <li key={precedent.id}>
                  <Link to={`/theses/${precedent.id}`}>
                    {precedent.run_date} {precedent.run_slot} · {precedent.label} · 상승{" "}
                    {percentText(precedent.prob_up)}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </Async>
  );
}
