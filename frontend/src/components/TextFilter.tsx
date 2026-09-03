// URL에 담기는 글자 필터. **한 글자마다 URL을 고치지 않는다.**
//
// 전에는 `value={q}` + `onChange`로 곧장 `setParams`를 불렀다. 그러면 한글이 깨진다 —
// `기준`을 치면 `ㄱㅣㅈㅜㄴ`이 된다.
//
// ## 왜 깨지나
//
// 한글 입력기는 낱자를 모아 글자를 만드는 **조합 중** 상태를 갖는다(`ㄱ` → `기` → `기ㅈ` →
// `기주` → `기준`). 그 사이 React가 다시 그리면서 input의 `value`를 **확정된 값**으로
// 되돌리면, 입력기는 조합을 이어 갈 자리를 잃고 낱자를 그대로 흘린다. 우리는 키 하나마다
// `setParams`로 라우터를 움직였으니 매 낱자마다 그 일이 일어났다.
//
// 곁가지로 두 가지가 더 있었다. **낱자마다 BM25 검색을 한 번씩** 쳤고(`기`·`기ㅈ`·`기주`…),
// 뒤로 가기 기록이 글자 수만큼 쌓였다.
//
// ## 그래서
//
// 초안은 이 컴포넌트가 들고, **Enter나 포커스를 잃을 때만** 밖으로 넘긴다. 조합 중에는
// Enter도 무시한다 — 그 Enter는 "검색해라"가 아니라 "이 글자로 확정해라"다.

import { useEffect, useRef, useState } from "react";

export default function TextFilter({
  label,
  value,
  onCommit,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  /** 확정된 값. **밖은 이때만 바뀐다.** */
  onCommit: (value: string) => void;
  placeholder?: string;
  type?: "text" | "search" | "number";
}) {
  const [draft, setDraft] = useState(value);
  // 조합 중인지. 이 값이 참일 때의 Enter는 글자 확정이지 제출이 아니다.
  const composing = useRef(false);

  // 밖에서 값이 바뀌면(뒤로 가기, 다른 화면에서 온 링크) 초안도 따라간다.
  useEffect(() => setDraft(value), [value]);

  const commit = () => {
    if (draft !== value) onCommit(draft);
  };

  return (
    <label>
      {label}
      <input
        type={type}
        value={draft}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
        }}
        onKeyDown={(event) => {
          if (event.key !== "Enter") return;
          // `isComposing`이 원본이고 ref는 그것을 못 주는 브라우저의 대비다.
          if (composing.current || event.nativeEvent.isComposing) return;
          event.preventDefault();
          commit();
        }}
        onBlur={commit}
      />
    </label>
  );
}
