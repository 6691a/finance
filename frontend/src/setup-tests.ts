// jsdom이 안 갖고 있는 브라우저 API를 채운다. **가짜(stub)가 아니라 폴리필이다** —
// `vi.stubGlobal`로 걸면 `vi.unstubAllGlobals()`가 그것을 걷어 가는데, Testing Library의
// 자동 cleanup afterEach가 우리 afterEach보다 **나중에** 돌아서 그 사이에 React가
// 마지막 effect를 흘려보내며 `ResizeObserver`를 찾는다. 그때 없으면 테스트가 무작위로
// 죽는다(2026-08-27에 8회 중 1~2회 꼴로 그랬다).
//
// 그래서 `globalThis`에 직접 심는다. 개별 테스트가 spy를 끼우려고 `stubGlobal`을 쓰면
// 그 테스트 안에서만 덮이고, 되돌릴 때 이 폴리필로 돌아온다.

class NoopResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = NoopResizeObserver;
}
