// 첫 화면. **"지금 무엇이 돌고 있나"를 한 눈에** 답한다.
//
// 이 화면은 새 API를 만들지 않는다 — 이미 있는 조회 다섯을 나란히 부르고 각각의 **첫 줄**만
// 읽는다. 대시보드 전용 집계 라우트를 만들면 그 라우트만 아는 규칙이 생기고, 화면이 바뀔
// 때마다 서버를 고쳐야 한다.
//
// **카드마다 그 화면으로 가는 링크가 있다.** 여기서 답이 끝나는 것이 아니라 어디를 볼지
// 정하는 자리다.

import { Link } from "react-router-dom";

import { query, useJson } from "../api";
import { integerText, kstText } from "../format";
import { CAUSAL_BIASES, labelOf } from "../labels";
import type {
  CausalDirectionRow,
  CausalPathRow,
  DocumentList,
  LlmRunList,
  Paged,
  SourceHealth,
  ThesisList,
} from "../types";

/** 오늘까지 N일 전. 카드마다 창이 다르다 — 수집은 하루, 추론은 그 주다. */
function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

function Card({ title, to, children }: { title: string; to: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <h3>
        <Link to={to}>{title}</Link>
      </h3>
      {children}
    </section>
  );
}

/** 값 한 줄. 숫자가 없으면 `—`이고 0으로 채우지 않는다. */
function Line({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <p className="state">
      {label} · <strong>{value}</strong>
    </p>
  );
}

export default function DashboardPage() {
  // **수집 요약은 늦은 것이 위다.** 첫 줄이 곧 "가장 오래 소식 없는 출처"다.
  const health = useJson<Paged<SourceHealth> & { since: string }>(
    "/api/collection/health?hours=24&limit=5",
  );
  const runs = useJson<LlmRunList>("/api/llm-runs?limit=5");
  const theses = useJson<ThesisList>("/api/theses?limit=5");
  const causal = useJson<Paged<CausalPathRow>>(
    `/api/causal/paths${query({ from: daysAgo(21), limit: 5 })}`,
  );
  const documents = useJson<DocumentList>(`/api/documents${query({ from: daysAgo(1), limit: 5 })}`);
  // **추론이 실제로 읽는 값이 이것이다.** 경로가 아니라 대상별로 접은 방향성이 관측 상태로 간다.
  const directions = useJson<Paged<CausalDirectionRow>>(
    `/api/causal/directions${query({ from: daysAgo(28), limit: 5 })}`,
  );

  const stale = health.data?.items[0] ?? null;
  const failed = (health.data?.items ?? []).filter((item) => item.failed > 0);
  const running = runs.data?.items.filter((item) => item.status === "running") ?? [];
  const latestRun = runs.data?.items[0] ?? null;
  const latestThesis = theses.data?.items[0] ?? null;
  const latestPath = causal.data?.items[0] ?? null;
  const latestDirection = directions.data?.items[0] ?? null;
  const unassessed = (documents.data?.items ?? []).filter((item) => item.assessed_at === null);

  return (
    <section>
      <h2>대시보드</h2>
      <p className="state">
        지금 무엇이 돌고 있나. **여기서 답이 끝나지 않는다** — 각 제목이 그 화면으로 가는 링크이고,
        숫자는 최근 몇 건만 본 것이다.
      </p>

      {/* **문서 검색은 뺐다**(2026-09-02). 첨부 색인이 조회 시점에 통째로 토크나이즈되어
          운영 DB를 붙잡았다. 다음 판은 쪽 단위 색인으로 새로 짓는다 — 설계 문서 §8.8. */}

      <div className="cards">
        <Card title="수집" to="/collection">
          {stale === null ? (
            <p className="state">최근 24시간에 수집 기록이 없다.</p>
          ) : (
            <>
              {/* 이 화면의 질문이 "무엇이 안 들어오고 있나"라서 가장 늦은 출처가 먼저다. */}
              <Line
                label="가장 오래 소식 없는 출처"
                value={`${stale.source} · ${stale.latest_at === null ? "—" : kstText(stale.latest_at)}`}
              />
              <Line
                label="실패가 있는 출처"
                value={failed.length === 0 ? "없음" : failed.map((item) => item.source).join(" · ")}
              />
            </>
          )}
        </Card>

        <Card title="LLM 실행" to="/runs">
          {latestRun === null ? (
            <p className="state">실행 기록이 없다.</p>
          ) : (
            <>
              <Line
                label="마지막 실행"
                value={`${latestRun.kind} · ${kstText(latestRun.started_at)}`}
              />
              {/* **running은 "지금 도는 중"이기도 하고 "끊긴 것"이기도 하다.** 세어서 보이되
                  둘을 가르지 않는다 — 이 기록만으로는 가를 수 없다. */}
              <Line
                label="종료를 기록 못 한 실행"
                value={running.length === 0 ? "없음" : `${running.length}건`}
              />
            </>
          )}
        </Card>

        <Card title="추론" to="/theses">
          {latestThesis === null ? (
            <p className="state">추론이 없다.</p>
          ) : (
            <>
              <Line
                label="마지막 추론"
                value={`${latestThesis.run_date} ${latestThesis.run_slot} · ${latestThesis.label}`}
              />
              <Line
                label="기준가"
                value={
                  latestThesis.base_price === null
                    ? "기록 전"
                    : integerText(latestThesis.base_price)
                }
              />
            </>
          )}
        </Card>

        <Card title="인과 그래프" to="/causal">
          {latestPath === null ? (
            <p className="state">최근 3주에 경로가 없다.</p>
          ) : (
            <>
              <Line label="마지막 주" value={latestPath.week_start} />
              {/* **예측이 아니라 사전 맥락이다** — 주 W를 W+2 월요일에 접으므로 추론이 보는
                  것은 최소 9일 전 인과다. 그 나이를 화면이 밝힌다. */}
              <Line
                label="접힌 방향성"
                value={
                  latestDirection === null
                    ? "아직 없음"
                    : `${latestDirection.week_start} · ${latestDirection.target_code} ${labelOf(CAUSAL_BIASES, latestDirection.bias)}`
                }
              />
              {/* 대상에서 출발한 경로가 있어야 주를 넘는 사슬이 선다. */}
              <Line
                label="대상에서 출발한 경로"
                value={`${(causal.data?.items ?? []).filter((row: CausalPathRow) => row.source_kind === "target").length}건(최근 5건 중)`}
              />
            </>
          )}
        </Card>

        <Card title="문서" to="/documents">
          {documents.data === null || documents.data.items.length === 0 ? (
            <p className="state">최근 하루에 문서가 없다.</p>
          ) : (
            <>
              <Line
                label="가장 최근 문서"
                value={`${documents.data.items[0]!.source_slug} · ${kstText(documents.data.items[0]!.published_at)}`}
              />
              {/* 미평가는 실패가 아니라 큐다. 0으로 채우지 않는 것과 같은 이유로 밝힌다. */}
              <Line
                label="아직 평가 안 된 문서"
                value={unassessed.length === 0 ? "없음" : `${unassessed.length}건(최근 5건 중)`}
              />
            </>
          )}
        </Card>
      </div>
    </section>
  );
}
