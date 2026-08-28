// 방향 표기. **낱말이 뜻을 지고 색은 거들 뿐이다.**

import { render } from "@testing-library/react";
import { expect, it } from "vitest";

import DirectionMark from "./DirectionMark";

it("셋을 한국어 낱말로 가른다", () => {
  render(
    <p>
      <DirectionMark value="positive" />
      <DirectionMark value="negative" />
      <DirectionMark value="neutral" />
    </p>,
  );

  expect(document.body.textContent).toBe("호재악재중립");
});

it("이모지를 섞지 않는다", () => {
  // 낱말이 이미 뜻을 다 진다. 이모지는 같은 말을 두 번 하는 것이고, 스크린리더와
  // 이모지 폰트가 없는 환경에서 위험만 는다.
  render(
    <p>
      <DirectionMark value="positive" />
      <DirectionMark value="negative" />
      <DirectionMark value="neutral" />
    </p>,
  );

  expect(document.body.textContent).toMatch(/^[가-힣]+$/);
});

it("색은 거들 뿐이다", () => {
  // 색만으로 방향을 구분하지 않는 것이 이 저장소의 규칙이다 — 낱말이 먼저다.
  const { container } = render(<DirectionMark value="positive" />);

  expect(container.querySelector(".up")).toBeTruthy();
  expect(container.textContent).toBe("호재");
});

it("값이 없으면 —다", () => {
  render(<DirectionMark value={null} />);

  expect(document.body.textContent).toBe("—");
});

it("모르는 값은 원본을 그대로 보인다", () => {
  // 수집 쪽에 새 방향이 생겼을 때 빈 칸으로 삼키면 아무도 눈치채지 못한다.
  render(<DirectionMark value="sideways" />);

  expect(document.body.textContent).toBe("sideways");
});
