// 종목코드를 사람이 읽는 이름으로.
//
// **`005930`은 사람이 읽는 값이 아니다.** 화면 여러 곳이 종목코드를 그대로 찍고 있었는데,
// 코드를 외운 사람만 읽을 수 있는 표가 된다. 이름의 원본은 `instrument` 마스터
// (`ticker` → `name`)이고, 그 마스터가 두 행뿐이라 화면이 한 번 받아 들고 있으면 된다.
//
// **응답마다 이름을 실어 보내지 않는다.** 그러려면 수급·사건·문서의 스키마 열몇 개에
// 같은 칸을 더해야 하고, 마스터가 바뀌면 그 전부가 따라 바뀐다. 이름은 표시용이라
// 화면이 붙이는 것이 맞다.
//
// **모르는 코드는 코드를 그대로 보인다.** 마스터에 없는 종목이 오면 빈 칸으로 삼키지
// 않는다 — `krx_credit_balance_ranking_daily`처럼 우리 추적 목록 밖 종목이 실제로 온다.

import { useJson } from "./api";
import type { InstrumentRow, Items } from "./types";

export type StockNames = Record<string, string>;

/**
 * `삼성전자(005930)`. 코드도 함께 남기는 이유는 **다른 화면·SQL과 맞춰 볼 때 필요한
 * 것이 코드**이기 때문이다 — 이름만 두면 필터에 무엇을 넣을지 알 수 없다.
 */
export function stockText(code: string | null, names: StockNames): string {
  if (code === null || code === "") return "—";
  const name = names[code];
  return name === undefined ? code : `${name}(${code})`;
}

/**
 * 종목 이름 표. 마스터가 두 행이라 화면마다 한 번씩 받아도 싸다.
 *
 * 실패해도 화면을 죽이지 않는다 — 이름은 장식이고 값은 이미 왔다. 그때는 코드가 그대로
 * 보이므로 표가 읽히지 않게 되지는 않는다.
 */
export function useStockNames(): StockNames {
  const resource = useJson<Items<InstrumentRow>>("/api/collection/instruments");
  const names: StockNames = {};
  for (const row of resource.data?.items ?? []) names[row.ticker] = row.name;
  return names;
}
