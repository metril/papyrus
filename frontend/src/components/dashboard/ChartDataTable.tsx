interface ChartDataTableProps {
  /** Describes the chart the table mirrors (read by screen readers). */
  caption: string;
  headers: string[];
  rows: (string | number)[][];
}

/**
 * Visually-hidden table mirroring a chart's data, so the information behind the
 * marks is available to screen readers and keyboard users. `sr-only` keeps it
 * off-screen but in the accessibility tree.
 */
export default function ChartDataTable({ caption, headers, rows }: ChartDataTableProps) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr>
          {headers.map((h) => (
            <th key={h} scope="col">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) =>
              j === 0 ? (
                <th key={j} scope="row">
                  {cell}
                </th>
              ) : (
                <td key={j}>{cell}</td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
