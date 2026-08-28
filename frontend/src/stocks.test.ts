import { describe, expect, it } from "vitest";

import { stockText } from "./stocks";

const NAMES = { "005930": "삼성전자", "000660": "SK하이닉스" };

describe("stockText", () => {
  it("이름과 코드를 함께 낸다", () => {
    // 이름만 두면 필터에 무엇을 넣을지 알 수 없다 — 코드가 다른 화면·SQL과 맞추는 키다.
    expect(stockText("005930", NAMES)).toBe("삼성전자(005930)");
  });

  it("마스터에 없는 코드는 코드를 그대로 보인다", () => {
    // 융자 순위처럼 우리 추적 목록 밖 종목이 실제로 온다. 빈 칸으로 삼키지 않는다.
    expect(stockText("035720", NAMES)).toBe("035720");
  });

  it("이름이 아직 안 왔어도 코드가 보인다", () => {
    // 이름은 장식이라 본 데이터를 막지 않는다.
    expect(stockText("005930", {})).toBe("005930");
  });

  it("빈 값은 —다", () => {
    expect(stockText(null, NAMES)).toBe("—");
    expect(stockText("", NAMES)).toBe("—");
  });
});
