// 쪽 이동. **아는 만큼만 번호를 그린다** — 서버가 총 건수를 세지 않는다.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import Pager, { pageNumbers, pageOf } from "./Pager";

it("쪽이 하나뿐이면 그리지 않는다", () => {
  const { container } = render(
    <Pager page={{ limit: 50, offset: 0, has_more: false }} onMove={vi.fn()} />,
  );
  expect(container.firstChild).toBeNull();
});

it("첫 쪽에서는 이전이 막히고 1·2가 보인다", () => {
  // 2쪽이 보이는 근거는 `has_more`뿐이다. 3쪽은 있는지 없는지 모르므로 안 그린다.
  render(<Pager page={{ limit: 50, offset: 0, has_more: true }} onMove={vi.fn()} />);

  expect(screen.getByRole("button", { name: /이전/ }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "1" }).getAttribute("aria-current")).toBe("page");
  expect(screen.getByRole("button", { name: "2" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "3" })).toBeNull();
});

it("마지막 쪽에서는 다음이 막힌다", () => {
  render(<Pager page={{ limit: 50, offset: 50, has_more: false }} onMove={vi.fn()} />);

  expect(screen.getByRole("button", { name: /다음/ }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "2" }).getAttribute("aria-current")).toBe("page");
});

it("번호를 누르면 그 쪽의 offset으로 간다", async () => {
  const move = vi.fn();
  render(<Pager page={{ limit: 50, offset: 100, has_more: true }} onMove={move} />);

  await userEvent.click(screen.getByRole("button", { name: "1" }));
  expect(move).toHaveBeenCalledWith(0);

  await userEvent.click(screen.getByRole("button", { name: "4" }));
  expect(move).toHaveBeenCalledWith(150);
});

it("이전·다음은 쪽 크기만큼 움직인다", async () => {
  const move = vi.fn();
  render(<Pager page={{ limit: 50, offset: 100, has_more: true }} onMove={move} />);

  await userEvent.click(screen.getByRole("button", { name: /다음/ }));
  expect(move).toHaveBeenCalledWith(150);

  await userEvent.click(screen.getByRole("button", { name: /이전/ }));
  expect(move).toHaveBeenCalledWith(50);
});

it("깊이 들어가면 앞을 접고 1쪽은 남긴다", () => {
  // 처음으로 돌아가는 것이 가장 흔한 이동이라 1쪽은 언제나 있다.
  expect(pageNumbers(9, 10)).toEqual([1, null, 7, 8, 9, 10]);
  expect(pageNumbers(3, 4)).toEqual([1, 2, 3, 4]);
  expect(pageNumbers(1, 1)).toEqual([1]);
});

it("쪽 칸이 없는 응답에서는 아무 것도 안 만든다", () => {
  // 곡선처럼 행 목록이 아닌 응답이 그렇다. 있는 척하면 없는 버튼이 생긴다.
  expect(pageOf({ countries: [] })).toBeNull();
  expect(pageOf(null)).toBeNull();
  expect(pageOf({ limit: 50, offset: 0 })).toEqual({ limit: 50, offset: 0, has_more: false });
});
