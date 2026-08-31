// 문서 하나. **원문과 LLM 평가를 한 화면에 둔다.**
//
// 본문은 React의 기본 text escaping으로 그린다 — `dangerouslySetInnerHTML`을 쓰지 않는다.
// 수집한 HTML이 그대로 실행되면 이 화면이 곧 남의 스크립트를 도는 자리가 된다.
//
// `assessed_content_hash`가 현재 `content_hash`와 다르면 본문이 평가 뒤에 바뀐 것이다.
// 화면이 그것을 밝힌다 — 평가가 옛 본문을 보고 매긴 점수일 수 있다.

import { Link, useParams } from "react-router-dom";

import { useJson } from "../api";
import { Async } from "../components/AsyncState";
import DirectionMark from "../components/DirectionMark";
import { integerText, jsonText, kstText, numberText, safeHref } from "../format";
import { ATTACHMENT_KINDS, BODY_STATUSES, labelOf } from "../labels";
import { stockText, useStockNames } from "../stocks";
import type { DocumentDetail } from "../types";

export default function DocumentDetailPage() {
  const { documentId } = useParams();
  const names = useStockNames();
  const resource = useJson<DocumentDetail>(`/api/documents/${documentId}`);

  return (
    <Async resource={resource} what="문서" back="/documents">
      {(document) => {
        const href = safeHref(document.canonical_url);
        const stale =
          document.assessed_content_hash !== null &&
          document.assessed_content_hash !== document.content_hash;
        return (
          <section>
            <h2 className="wrap">{document.title}</h2>
            <nav className="pager" aria-label="관련 화면">
              <Link to="/documents">문서 목록으로</Link>
              {href !== null && (
                <a href={href} target="_blank" rel="noopener noreferrer">
                  원문 열기
                </a>
              )}
            </nav>

            <div className="panel">
              <dl className="meta">
                <dt>출처</dt>
                <dd>
                  {document.source_slug} · {document.document_type}
                </dd>
                <dt>발행(KST)</dt>
                <dd>
                  <time dateTime={document.published_at}>{kstText(document.published_at)}</time>
                </dd>
                <dt>감지(KST)</dt>
                <dd>
                  <time dateTime={document.detected_at}>{kstText(document.detected_at)}</time>
                </dd>
                <dt>수집 범위</dt>
                <dd>{labelOf(BODY_STATUSES, document.body_status)}</dd>
                <dt>언어</dt>
                <dd>{document.language ?? "—"}</dd>
                <dt>태그</dt>
                <dd className="wrap">
                  {[
                    ...document.instruments.map((code) => stockText(code, names)),
                    ...document.indicators,
                  ].join(" · ") || "—"}
                </dd>
              </dl>
            </div>

            <h3>LLM 평가</h3>
            {document.assessed_at === null ? (
              <p className="state">
                아직 평가하지 않았다. **점수가 낮은 것이 아니라 안 본 것이다** — 평가에 실패한
                문서는 다음 정시 실행이 다시 집는다.
              </p>
            ) : (
              <div className="panel">
                <div className="readout">
                  <span className="value">{numberText(document.value_score, 0)}</span>
                  <span>
                    방향 <DirectionMark value={document.direction} /> · {document.llm_model ?? "—"}{" "}
                    · {document.prompt_version ?? "—"} ·{" "}
                    <time dateTime={document.assessed_at}>{kstText(document.assessed_at)}</time>
                  </span>
                </div>
                {document.summary !== null && (
                  <dl className="meta">
                    <dt>요약</dt>
                    <dd className="wrap">{document.summary}</dd>
                    <dt>평가 근거</dt>
                    <dd className="wrap">
                      {/* **모양을 고정하지 않는다.** 판이 바뀌며 칸이 늘어나는 값이라
                          우리가 아는 칸만 그리면 새 칸이 조용히 사라진다. */}
                      {document.assessment === null ? (
                        "—"
                      ) : (
                        <pre>
                          <code>{jsonText(document.assessment)}</code>
                        </pre>
                      )}
                    </dd>
                  </dl>
                )}
                {stale && (
                  <p className="warn">
                    평가 뒤에 본문이 바뀌었다. 이 점수는 옛 본문을 보고 매긴 것이다.
                  </p>
                )}
              </div>
            )}

            <h3>본문</h3>
            {document.body === null ? (
              <p className="state">
                {/* **null은 "아직 안 해 봤다"이고 그 집합이 곧 수집 큐다.** 못 받은 사유와
                    구별해서 말한다 — 둘을 뭉치면 다시 집을 것과 아닌 것이 섞인다. */}
                {document.body_status === null
                  ? "아직 본문을 받아 보지 않았다. 다음 실행이 집는다."
                  : `본문이 없다(${labelOf(BODY_STATUSES, document.body_status)}).`}
                {document.body_status === "attachment_only" && " 내용은 아래 첨부에 있다."}
              </p>
            ) : (
              <pre>
                <code>{document.body}</code>
              </pre>
            )}

            <h3>첨부 {document.attachments.length}개</h3>
            {document.attachments.length === 0 ? (
              <p className="state">첨부가 없다.</p>
            ) : (
              <table>
                <caption>
                  문서 안에 나온 차례다. **저장 경로는 내지 않는다** — 마운트 안의 상대경로라
                  화면에서 열 수 있는 주소가 아니다.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">종류</th>
                    <th scope="col" className="wrap">
                      이름
                    </th>
                    <th scope="col">형식</th>
                    <th scope="col">크기</th>
                    <th scope="col">보관</th>
                    <th scope="col">원본</th>
                  </tr>
                </thead>
                <tbody>
                  {document.attachments.map((row) => {
                    const href = safeHref(row.url);
                    return (
                      <tr key={row.position}>
                        <td>{row.position}</td>
                        <td>{labelOf(ATTACHMENT_KINDS, row.kind)}</td>
                        <td className="wrap">{row.filename ?? "—"}</td>
                        <td>{row.media_type ?? "—"}</td>
                        <td>{row.byte_size === null ? "—" : `${integerText(row.byte_size)} B`}</td>
                        <td>{row.stored ? "받음" : "—"}</td>
                        <td>
                          {href === null ? (
                            "—"
                          ) : (
                            <a href={href} target="_blank" rel="noopener noreferrer">
                              열기
                            </a>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>
        );
      }}
    </Async>
  );
}
