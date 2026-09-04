// 클라이언트 라우트와 공통 shell.
//
// **첫 화면은 대시보드이고 추론의 얼굴은 `/forecast`다.** 대상이 코스피 하나로 좁아져서
// "오늘 무엇이라고 말했나"가 곧 그 기능의 전부다.
//
// 원자료 여섯(시세·지표·문서·수급·사건·수집)은 **추론이 딛고 선 것**이라 내비게이션에서
// 추론 화면(전망·관계·메모·실행·품질) 앞에 둔다.

import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import Boundary from "./components/Boundary";
import CollectionPage from "./pages/CollectionPage";
import CurvePage from "./pages/CurvePage";
import DashboardPage from "./pages/DashboardPage";
import DocumentDetailPage from "./pages/DocumentDetailPage";
import DocumentsPage from "./pages/DocumentsPage";
import EventsPage from "./pages/EventsPage";
import ForecastDetailPage from "./pages/ForecastDetailPage";
import ForecastPage from "./pages/ForecastPage";
import IndicatorDetailPage from "./pages/IndicatorDetailPage";
import IndicatorsPage from "./pages/IndicatorsPage";
import MemoriesPage from "./pages/MemoriesPage";
import PositioningPage from "./pages/PositioningPage";
import QualityPage from "./pages/QualityPage";
import QuoteDetailPage from "./pages/QuoteDetailPage";
import QuotesPage from "./pages/QuotesPage";
import RelationDetailPage from "./pages/RelationDetailPage";
import RelationsPage from "./pages/RelationsPage";
import RunDetailPage from "./pages/RunDetailPage";
import RunsPage from "./pages/RunsPage";
import ToolCallPage from "./pages/ToolCallPage";

export default function App() {
  return (
    <div className="shell">
      <header>
        <h1>추론 추적</h1>
        <nav aria-label="주요 화면">
          <NavLink to="/dashboard">대시보드</NavLink>
          <NavLink to="/quotes">시세</NavLink>
          <NavLink to="/indicators">지표</NavLink>
          <NavLink to="/documents">문서</NavLink>
          <NavLink to="/positioning">수급</NavLink>
          <NavLink to="/events">사건</NavLink>
          <NavLink to="/collection">수집</NavLink>
          <NavLink to="/forecast">전망</NavLink>
          <NavLink to="/relations">관계</NavLink>
          <NavLink to="/memories">메모</NavLink>
          <NavLink to="/runs">실행</NavLink>
          <NavLink to="/quality">품질</NavLink>
        </nav>
      </header>
      <main>
        {/* 렌더 예외가 흰 화면이 되지 않게 한다. 무엇이 터졌는지 그 자리에 남는다. */}
        <Boundary>
          <Routes>
            {/* **첫 화면이 대시보드다.** 실행 목록은 "무슨 일이 있었나"의 한 갈래일
                뿐이라 수집·전망·관계가 안 보인다. */}
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
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
            {/* 정적 경로가 동적 경로보다 먼저 온다 — 뒤면 `memories`가 요인 코드로 물린다. */}
            <Route path="/forecast" element={<ForecastPage />} />
            <Route path="/forecast/:runDate/:slot" element={<ForecastDetailPage />} />
            <Route path="/relations" element={<RelationsPage />} />
            <Route path="/relations/:factor" element={<RelationDetailPage />} />
            <Route path="/memories" element={<MemoriesPage />} />
            <Route path="/runs" element={<RunsPage />} />
            <Route path="/runs/:llmRunId" element={<RunDetailPage />} />
            <Route path="/runs/:llmRunId/tool-calls/:seq" element={<ToolCallPage />} />
            <Route path="/quality" element={<QualityPage />} />
              <Route path="*" element={<p className="state">없는 화면이다.</p>} />
          </Routes>
        </Boundary>
      </main>
    </div>
  );
}
