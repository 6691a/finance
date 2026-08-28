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
import { kstText, numberText, safeHref } from "../format";
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
                <dd>{document.content_level}</dd>
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
                    방향 <DirectionMark value={document.direction} /> · {document.llm_model ?? "—"} ·{" "}
                    {document.prompt_version ?? "—"} ·{" "}
                    <time dateTime={document.assessed_at}>{kstText(document.assessed_at)}</time>
                  </span>
                </div>
                {document.summary !== null && (
                  <dl className="meta">
                    <dt>요약</dt>
                    <dd className="wrap">{document.summary}</dd>
                    <dt>평가 근거</dt>
                    <dd className="wrap">{document.assessment ?? "—"}</dd>
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
                본문을 받지 않는 출처다(`{document.content_level}`). 제목과 링크만 있다.
              </p>
            ) : (
              <pre>
                <code>{document.body}</code>
              </pre>
            )}
          </section>
        );
      }}
    </Async>
  );
}
