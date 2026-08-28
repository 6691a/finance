// 쪽 이동. **아는 만큼만 번호를 그린다.**
//
// 서버가 총 건수를 세지 않는다(`count(*)`가 목록 조회보다 비싸다). 지금 쪽까지는 확실히
// 있고 `has_more`면 그 다음 한 쪽이 더 있다는 것까지만 안다 — 번호도 딱 거기까지다.
// 마지막 쪽으로 건너뛰는 버튼이 없는 이유도 같다: 그리려면 총 쪽 수를 알아야 한다.

/** 응답에서 쪽 칸만 뽑아 본 모양. 어느 목록 응답이든 이 셋을 갖는다. */
export interface PageState {
  limit: number;
  offset: number;
  has_more: boolean;
}

/** 지금 쪽 좌우로 몇 개까지 번호를 보일지. 깊이 들어가도 줄이 길어지지 않게 한다. */
const WINDOW = 2;

/** 응답이 쪽 칸을 갖고 있으면 그것을, 아니면 `null`. */
export function pageOf(data: unknown): PageState | null {
  if (data === null || typeof data !== "object") return null;
  const found = data as Partial<PageState>;
  if (typeof found.limit !== "number" || typeof found.offset !== "number") return null;
  return {
    limit: found.limit,
    offset: found.offset,
    has_more: found.has_more === true,
  };
}

/**
 * 그릴 번호들. `null`은 생략 표시(…)다.
 *
 * 1쪽은 언제나 넣는다 — 깊이 들어간 뒤 처음으로 돌아가는 것이 가장 흔한 이동이다.
 */
export function pageNumbers(current: number, known: number): (number | null)[] {
  const wanted = new Set<number>([1, known]);
  for (let page = current - WINDOW; page <= current + WINDOW; page += 1) {
    if (page >= 1 && page <= known) wanted.add(page);
  }
  const sorted = [...wanted].sort((left, right) => left - right);

  const found: (number | null)[] = [];
  for (const [index, page] of sorted.entries()) {
    const previous = sorted[index - 1];
    if (previous !== undefined && page - previous > 1) found.push(null);
    found.push(page);
  }
  return found;
}

export default function Pager({
  page,
  onMove,
}: {
  page: PageState | null;
  onMove: (offset: number) => void;
}) {
  if (page === null) return null;
  if (page.offset === 0 && !page.has_more) return null;

  const current = Math.floor(page.offset / page.limit) + 1;
  // **여기까지는 있는 것이 확실하다.** 그 너머는 서버가 세지 않아 모른다.
  const known = current + (page.has_more ? 1 : 0);

  return (
    <nav className="pager" aria-label="쪽 이동">
      <button
        type="button"
        disabled={current === 1}
        onClick={() => onMove(Math.max(0, page.offset - page.limit))}
      >
        ← 이전
      </button>

      <ol>
        {pageNumbers(current, known).map((number, index) =>
          number === null ? (
            // 생략 자리는 누를 것이 없다. key가 자리라 번호가 바뀌어도 흔들리지 않는다.
            <li key={`gap-${index}`} aria-hidden="true">
              …
            </li>
          ) : (
            <li key={number}>
              <button
                type="button"
                className={number === current ? "here" : ""}
                aria-current={number === current ? "page" : undefined}
                onClick={() => onMove((number - 1) * page.limit)}
              >
                {number}
              </button>
            </li>
          ),
        )}
      </ol>

      <button
        type="button"
        disabled={!page.has_more}
        onClick={() => onMove(page.offset + page.limit)}
      >
        다음 →
      </button>
    </nav>
  );
}
