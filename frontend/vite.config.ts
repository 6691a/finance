import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발에서만 쓰는 프록시다. 운영은 FastAPI가 같은 origin에서 `dist`를 제공하므로
// CORS 설정이 필요 없다. `/api`와 `/healthz`만 보낸다 — 그 밖의 경로는 클라이언트
// 라우트라 Vite가 index.html을 돌려줘야 한다.
const API = "http://127.0.0.1:18000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: API, changeOrigin: false },
      "/healthz": { target: API, changeOrigin: false },
    },
  },
  // `globals: true`라 Testing Library가 afterEach cleanup을 스스로 건다. setup 파일이
  // 하는 일은 그것이 아니라 **jsdom이 안 갖고 있는 브라우저 API를 채우는 것**이다.
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/setup-tests.ts"],
    css: false,
  },
});
