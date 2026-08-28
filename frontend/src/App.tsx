// 클라이언트 라우트와 공통 shell.
//
// **화면 루트는 `/runs`다.** 실패한 실행에는 thesis가 없어서 실행 목록이 "무슨 일이
// 있었나"를 가장 넓게 답한다. `/theses`는 12단계 목록 API를 그대로 쓰는 보조 탐색 경로다.
//
// 15단계가 원자료 여섯을 더했다(시세·지표·문서·수급·사건·수집). 그쪽은 **추론이 딛고 선
// 원자료**라 내비게이션에서 추론 화면(실행·판단·품질) 앞에 둔다.

import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import Boundary from "./components/Boundary";
import CausalDetailPage from "./pages/CausalDetailPage";
import CausalPage from "./pages/CausalPage";
import CollectionPage from "./pages/CollectionPage";
import CurvePage from "./pages/CurvePage";
import DocumentDetailPage from "./pages/DocumentDetailPage";
import DocumentsPage from "./pages/DocumentsPage";
import EventsPage from "./pages/EventsPage";
import IndicatorDetailPage from "./pages/IndicatorDetailPage";
import IndicatorsPage from "./pages/IndicatorsPage";
import PositioningPage from "./pages/PositioningPage";
import QualityPage from "./pages/QualityPage";
import QuoteDetailPage from "./pages/QuoteDetailPage";
import QuotesPage from "./pages/QuotesPage";
import RunDetailPage from "./pages/RunDetailPage";
import RunsPage from "./pages/RunsPage";
import ThesesPage from "./pages/ThesesPage";
import ThesisDetailPage from "./pages/ThesisDetailPage";
import ThesisGraphPage from "./pages/ThesisGraphPage";
import ToolCallPage from "./pages/ToolCallPage";

export default function App() {
  return (
    <div className="shell">
      <header>
        <h1>추론 추적</h1>
        <nav aria-label="주요 화면">
          <NavLink to="/quotes">시세</NavLink>
          <NavLink to="/indicators">지표</NavLink>
          <NavLink to="/documents">문서</NavLink>
          <NavLink to="/positioning">수급</NavLink>
          <NavLink to="/events">사건</NavLink>
          <NavLink to="/collection">수집</NavLink>
          <NavLink to="/causal">인과</NavLink>
          <NavLink to="/runs">실행</NavLink>
          <NavLink to="/theses">판단</NavLink>
          <NavLink to="/quality">품질</NavLink>
        </nav>
      </header>
      <main>
        {/* 렌더 예외가 흰 화면이 되지 않게 한다. 무엇이 터졌는지 그 자리에 남는다. */}
        <Boundary>
          <Routes>
            <Route path="/" element={<Navigate to="/runs" replace />} />
            <Route path="/quotes" element={<QuotesPage />} />
            <Route path="/quotes/:kind/:symbol" element={<QuoteDetailPage />} />
            {/* `/curve`가 `/:provider/:seriesId`보다 먼저 온다 — 정적 경로가 뒤면 provider로 물린다. */}
            <Route path="/indicators" element={<IndicatorsPage />} />
            <Route path="/indicators/curve" element={<CurvePage />} />
            <Route path="/indicators/:provider/:seriesId" element={<IndicatorDetailPage />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/documents/:documentId" element={<DocumentDetailPage />} />
            <Route path="/positioning" element={<PositioningPage />} />
            <Route path="/events" element={<EventsPage />} />
            <Route path="/collection" element={<CollectionPage />} />
            <Route path="/causal" element={<CausalPage />} />
            <Route path="/causal/:pathId" element={<CausalDetailPage />} />
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/runs/:llmRunId" element={<RunDetailPage />} />
            <Route path="/runs/:llmRunId/tool-calls/:seq" element={<ToolCallPage />} />
            <Route path="/theses" element={<ThesesPage />} />
            <Route path="/theses/:thesisId" element={<ThesisDetailPage />} />
            <Route path="/theses/:thesisId/graph" element={<ThesisGraphPage />} />
            <Route path="/quality" element={<QualityPage />} />
              <Route path="*" element={<p className="state">없는 화면이다.</p>} />
          </Routes>
        </Boundary>
      </main>
    </div>
  );
}
