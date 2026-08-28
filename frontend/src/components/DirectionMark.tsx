// 문서의 방향 한 칸. `positive` → `호재`.
//
// **이모지를 쓰지 않는다.** 낱말이 이미 뜻을 다 지고 있어서 이모지는 같은 말을 두 번
// 하는 것이고, 스크린리더가 `➕`를 "plus sign"으로 읽거나 이모지 폰트가 없는 환경에서
// 두부(□)가 되는 위험만 는다. 색(`.up`/`.down`)은 거들 뿐이라 없어도 읽힌다 —
// "색만으로 성공·실패·방향을 구분하지 않는다"가 이 저장소의 규칙이다.
//
// **모르는 값은 원본을 그대로 보인다.** 수집 쪽에 새 방향이 생겼을 때 화면이 그것을
// 빈 칸으로 삼키면 아무도 눈치채지 못한다.

const MARKS: Record<string, { label: string; tone: string }> = {
  positive: { label: "호재", tone: "up" },
  negative: { label: "악재", tone: "down" },
  neutral: { label: "중립", tone: "" },
};

export default function DirectionMark({ value }: { value: string | null }) {
  if (value === null) return <>—</>;
  const mark = MARKS[value];
  if (mark === undefined) return <>{value}</>;
  return <span className={mark.tone}>{mark.label}</span>;
}
