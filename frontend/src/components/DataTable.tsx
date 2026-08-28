// 행 묶음 하나를 접근 가능한 표로. **화면마다 `<table>`을 다시 쓰지 않는다.**
//
// 이 컴포넌트를 두는 이유는 추상화가 좋아서가 아니라 **지금 스무 개 가까운 데이터셋이
// 같은 표를 요구하기 때문이다.** caption·thead·scope와 숫자 정렬 규칙을 스무 번 복사하면
// 그중 하나는 반드시 빠진다.
//
// 값은 `ReactNode`로 받는다 — 단위를 붙이거나 링크를 거는 것은 데이터셋이 정하고, 여기는
// 그것을 그릴 뿐이다.

import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  label: string;
  value: (row: T) => ReactNode;
  /** 긴 문장이라 줄바꿈을 허용할 칸. 기본은 한 줄이다. */
  wrap?: boolean;
}

export interface DataTableProps<T> {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  /** 행이 0개일 때의 문장. **"없다"와 "아직 안 불렀다"를 화면이 가른다.** */
  empty: string;
  /** 행의 안정적인 키. 없으면 순번을 쓴다 — 읽기 전용이라 재정렬이 없다. */
  rowKey?: (row: T, index: number) => string;
}

export default function DataTable<T>({
  caption,
  columns,
  rows,
  empty,
  rowKey,
}: DataTableProps<T>) {
  if (rows.length === 0) return <p className="state">{empty}</p>;
  return (
    <table>
      <caption>
        {caption} · {rows.length.toLocaleString("ko-KR")}행
      </caption>
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.key} scope="col" className={column.wrap ? "wrap" : undefined}>
              {column.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={rowKey ? rowKey(row, index) : index}>
            {columns.map((column) => (
              <td key={column.key} className={column.wrap ? "wrap" : undefined}>
                {column.value(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
