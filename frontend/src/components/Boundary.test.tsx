// 흰 화면 대신 무엇이 터졌는지 보인다.

import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import Boundary from "./Boundary";

function Broken(): never {
  throw new Error("Cannot read properties of undefined (reading 'length')");
}

it("렌더 예외를 화면에 남긴다", () => {
  // React가 경계까지 올라온 예외를 콘솔에 다시 찍는다. 테스트 출력만 조용히 시킨다.
  const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
  render(
    <Boundary>
      <Broken />
    </Boundary>,
  );

  expect(screen.getByRole("alert").textContent).toContain("reading 'length'");
  quiet.mockRestore();
});

it("터지지 않으면 그대로 통과시킨다", () => {
  render(
    <Boundary>
      <p>본문</p>
    </Boundary>,
  );
  expect(screen.getByText("본문")).toBeTruthy();
});
