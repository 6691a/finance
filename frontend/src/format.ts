// 표시용 변환. **원본 값은 바꾸지 않는다** — `<time datetime>`에 UTC 원문을 그대로 남기고
// 사람이 읽는 칸만 KST로 그린다.
//
// 서버가 주는 시각은 전부 UTC `Z`이고 시간대 변환은 프런트 몫이라는 것이 프로젝트 규칙이다.

const KST = "Asia/Seoul";

const DATE_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: KST,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: KST,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** `2026. 08. 26. 12:35:00` (KST). 값이 없으면 `—`다 — 빈 칸은 "없다"로 안 읽힌다. */
export function kstText(value: string | null | undefined): string {
  return value ? DATE_TIME.format(new Date(value)) : "—";
}

/** 시각만. 같은 날의 타임라인처럼 날짜가 이미 위에 적힌 자리에 쓴다. */
export function kstTimeText(value: string | null | undefined): string {
  return value ? TIME.format(new Date(value)) : "—";
}

const AXIS = new Intl.DateTimeFormat("en-GB", {
  timeZone: KST,
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/**
 * 차트 x축용 짧은 표기(`08/27 09:05`, KST).
 *
 * **조각으로 조립한다.** 로케일마다 구분자가 달라(`08. 27.` / `27/08,`) 결과 문자열을
 * 치환하면 언젠가 어긋난다. 축에는 연도를 적지 않는다 — 눈금이 촘촘해 겹치고, 어느
 * 해인지는 화면 위쪽 기준 시각이 이미 말한다.
 */
export function kstAxisText(value: string): string {
  const parts = Object.fromEntries(
    AXIS.formatToParts(new Date(value)).map((part) => [part.type, part.value]),
  );
  return `${parts["month"]}/${parts["day"]} ${parts["hour"]}:${parts["minute"]}`;
}

/** 밀리초를 사람이 읽는 소요로. 1초 미만은 밀리초 그대로 둔다. */
export function durationText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 소수 자리를 고정한 숫자. **`null`은 0이 아니라 `—`다.** */
export function numberText(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

/** 확률을 퍼센트로. 소수점 한 자리다. */
export function percentText(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

/** 등락률처럼 이미 퍼센트인 값. 부호를 유지한다. */
export function signedPercentText(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function integerText(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("ko-KR");
}

/**
 * 외부 링크로 써도 되는 주소인가. **`http:`·`https:`만 통과한다.**
 *
 * `javascript:`가 anchor의 `href`로 들어가면 클릭이 스크립트 실행이 된다. 통과하지 못한
 * 값은 링크가 아니라 text로 보여 준다.
 */
export function safeHref(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}

/** JSON이면 들여쓰고, 아니면 원문 그대로. **파싱 실패는 화면 오류가 아니다.** */
export function prettyJson(value: string | null | undefined): string {
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

/** 객체를 그대로 들여쓴 JSON으로. 인자와 `input_state`가 이 모양이다. */
export function jsonText(value: unknown): string {
  return JSON.stringify(value ?? null, null, 2);
}
