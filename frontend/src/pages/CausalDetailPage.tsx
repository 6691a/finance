// 인과 경로 하나의 상세. **한 사건이 한 판이다** — 그 사건이 그 주에 뻗은 경로 전부를
// 한 그래프에 그리고, 지금 보고 있는 경로만 방향 색으로 강조한다.
//
// 형제 전부에 색을 주면 한 화면에서 위·아래가 뒤섞여 어느 것이 이 경로의 주장인지 사라진다.
//
// **canvas만으로는 스크린리더가 관계를 못 읽는다.** 같은 내용을 아래 표로도 준다 —
// 관계 그래프 화면과 같은 규칙이다.

import { useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useJson } from "../api";
import { Async } from "../components/AsyncState";
import GraphView, { type GraphViewHandle } from "../components/GraphView";
import { causalElements } from "../causal";
import {
  CAUSAL_CONFIDENCES,
  CAUSAL_EVIDENCE_KINDS,
  CAUSAL_SIGNS,
  CAUSAL_SOURCE_KINDS,
  CAUSAL_TARGET_KINDS,
  labelOf,
} from "../labels";
import type { CausalEvidenceRow, CausalPathDetail, CausalPathRow } from "../types";
import { changeText, chainText, sourceText } from "./CausalPage";

/** 근거 한 줄의 보일 이름. 문서만 제목이 오고 나머지는 식별자가 곧 읽을 수 있는 값이다. */
export function evidenceText(row: CausalEvidenceRow): string {
  return row.title ?? row.ref;
}

/** 이 경로가 무엇을 주장하는지 한 줄. 표와 그래프가 같은 말을 해야 한다. */
export function claimText(row: CausalPathRow): string {
  return `${sourceText(row)} → ${labelOf(CAUSAL_TARGET_KINDS, row.target_kind)} ${row.target_code}를 ${labelOf(CAUSAL_SIGNS, row.sign)}`;
}

export default function CausalDetailPage() {
  const { pathId } = useParams();
  const resource = useJson<CausalPathDetail>(`/api/causal/paths/${pathId}`);
  const [selected, setSelected] = useState<string | null>(null);
  const handle = useRef<GraphViewHandle | null>(null);

  const elements = useMemo(
    () =>
      resource.data === null ? [] : causalElements(resource.data.siblings, resource.data.path),
    [resource.data],
  );

  return (
    <Async resource={resource} what="인과 경로" back="/causal">
      {(detail) => (
        <section>
          <h2>인과 경로</h2>
          <nav className="pager" aria-label="관련 화면">
            <Link to="/causal">목록으로</Link>
          </nav>
          <p className="state">
            **인과의 증명이 아니다.** `함께 관찰`은 같은 기간에 함께 움직였다는 뜻이고 `해석`은
            모델의 말이다.
          </p>

          <dl className="meta">
            <dt>주장</dt>
            <dd>{claimText(detail.path)}</dd>
            <dt>주 · 출발점</dt>
            <dd>
              {detail.path.week_start} · {labelOf(CAUSAL_SOURCE_KINDS, detail.path.source_kind)}
              {detail.path.event_occurred_on === null
                ? ""
                : ` · 사건 발생 ${detail.path.event_occurred_on}`}
            </dd>
            <dt>체인</dt>
            <dd>{chainText(detail.path)}</dd>
            <dt>근거의 성격</dt>
            <dd>{labelOf(CAUSAL_CONFIDENCES, detail.path.confidence)}</dd>
            <dt>실현 등락(그 주 · T+1 · T+5)</dt>
            <dd>
              {changeText(detail.path.return_week_change, detail.path.return_unit)} ·{" "}
              {changeText(detail.path.return_t1_change, detail.path.return_unit)} ·{" "}
              {changeText(detail.path.return_t5_change, detail.path.return_unit)}
            </dd>
            <dt>설명</dt>
            <dd className="wrap">{detail.path.reasoning}</dd>
          </dl>

          <div className="filters">
            <button type="button" onClick={() => handle.current?.fit()}>
              화면에 맞추기
            </button>
            <button type="button" onClick={() => handle.current?.reset()}>
              초기화
            </button>
          </div>

          <div className="graph-layout">
            <GraphView
              elements={elements}
              selected={selected}
              onSelect={setSelected}
              handleRef={handle}
            />
            <aside className="panel" aria-label="이 주의 경로">
              <h3>이 주의 경로 {detail.siblings.length}개</h3>
              <ul>
                {detail.siblings.map((row) => (
                  <li key={row.id}>
                    {row.id === detail.path.id ? (
                      <strong>{claimText(row)}</strong>
                    ) : (
                      <Link to={`/causal/${row.id}`}>{claimText(row)}</Link>
                    )}
                  </li>
                ))}
              </ul>
            </aside>
          </div>

          <h3>이 경로가 든 근거</h3>
          {(() => {
            const mine = detail.evidence.filter((row) => row.path_id === detail.path.id);
            // **근거가 없는 경로도 있다.** 0으로 채우거나 감추면 `함께 관찰`이 무엇에
            // 기대고 있는지 되짚을 수 없다 — 없다는 것도 사실이다.
            return mine.length === 0 ? (
              <p className="state">이 경로에 저장된 근거가 없다.</p>
            ) : (
              <ul>
                {mine.map((row) => (
                  <li key={row.ref}>
                    <span className="badge">{labelOf(CAUSAL_EVIDENCE_KINDS, row.kind)}</span>{" "}
                    {row.url === null ? (
                      evidenceText(row)
                    ) : row.url.startsWith("/") ? (
                      <Link to={row.url}>{evidenceText(row)}</Link>
                    ) : (
                      <a href={row.url} target="_blank" rel="noopener noreferrer">
                        {evidenceText(row)}
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            );
          })()}

          <h3>같은 내용의 표</h3>
          <table>
            <caption>graph canvas와 같은 집합이다. 굵은 줄이 지금 보고 있는 경로다.</caption>
            <thead>
              <tr>
                <th scope="col">체인</th>
                <th scope="col">방향</th>
                <th scope="col">근거</th>
                <th scope="col">그 주</th>
                <th scope="col">T+1</th>
                <th scope="col">T+5</th>
              </tr>
            </thead>
            <tbody>
              {detail.siblings.map((row) => (
                <tr key={row.id}>
                  <td className="wrap">
                    {row.id === detail.path.id ? (
                      <strong>{chainText(row)}</strong>
                    ) : (
                      <Link to={`/causal/${row.id}`}>{chainText(row)}</Link>
                    )}
                  </td>
                  <td>
                    <span className={row.sign === "up" ? "up" : "down"}>
                      {labelOf(CAUSAL_SIGNS, row.sign)}
                    </span>
                  </td>
                  <td>{labelOf(CAUSAL_CONFIDENCES, row.confidence)}</td>
                  <td>{changeText(row.return_week_change, row.return_unit)}</td>
                  <td>{changeText(row.return_t1_change, row.return_unit)}</td>
                  <td>{changeText(row.return_t5_change, row.return_unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </Async>
  );
}
