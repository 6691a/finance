// URL에 담기는 글자 필터.
//
// **한글이 깨지던 자리다**(2026-09-01 사용자 보고 — `기준`을 치면 `ㄱㅣㅈㅜㄴ`). 키마다
// `setParams`를 부르면 조합 중인 글자가 확정 값으로 되돌아가면서 낱자가 흘러나온다.

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import TextFilter from "./TextFilter";

it("치는 동안에는 밖으로 안 내보낸다", async () => {
  // 낱자마다 내보내면 URL이 글자 수만큼 바뀌고 검색도 그만큼 나간다.
  const commit = vi.fn();
  render(<TextFilter label="검색" value="" onCommit={commit} />);

  await userEvent.type(screen.getByLabelText("검색"), "기준금리");

  expect(commit).not.toHaveBeenCalled();
  expect(screen.getByLabelText("검색")).toHaveProperty("value", "기준금리");
});

it("Enter로 확정한다", async () => {
  const commit = vi.fn();
  render(<TextFilter label="검색" value="" onCommit={commit} />);

  const input = screen.getByLabelText("검색");
  await userEvent.type(input, "기준금리{Enter}");

  expect(commit).toHaveBeenCalledWith("기준금리");
});

it("조합 중의 Enter는 확정이 아니다", () => {
  // 그 Enter는 "검색해라"가 아니라 "이 글자로 확정해라"다. 여기서 제출하면 `기ㅈ`이 나간다.
  const commit = vi.fn();
  render(<TextFilter label="검색" value="" onCommit={commit} />);

  const input = screen.getByLabelText("검색");
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: "기ㅈ" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });

  expect(commit).not.toHaveBeenCalled();

  fireEvent.compositionEnd(input);
  fireEvent.change(input, { target: { value: "기준" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(commit).toHaveBeenCalledWith("기준");
});

it("포커스를 잃을 때도 확정한다", async () => {
  // Enter를 안 누르고 다른 칸으로 가는 사람이 있다.
  const commit = vi.fn();
  render(<TextFilter label="검색" value="" onCommit={commit} />);

  await userEvent.type(screen.getByLabelText("검색"), "반도체");
  await userEvent.tab();

  expect(commit).toHaveBeenCalledWith("반도체");
});

it("값이 그대로면 내보내지 않는다", async () => {
  // 훑고 지나가는 포커스가 요청을 만들면 안 된다.
  const commit = vi.fn();
  render(<TextFilter label="검색" value="기준금리" onCommit={commit} />);

  await userEvent.click(screen.getByLabelText("검색"));
  await userEvent.tab();

  expect(commit).not.toHaveBeenCalled();
});

it("밖에서 값이 바뀌면 따라간다", () => {
  // 뒤로 가기나 링크로 들어온 경우다.
  const view = render(<TextFilter label="검색" value="기준금리" onCommit={vi.fn()} />);
  view.rerender(<TextFilter label="검색" value="반도체" onCommit={vi.fn()} />);

  expect(screen.getByLabelText("검색")).toHaveProperty("value", "반도체");
});
