import { SERIES, type ChartSeries } from './chartTheme';

interface ChartLegendProps {
  /** Series to key, in order. Defaults to the print/scan pair. */
  series?: readonly ChartSeries[];
}

/**
 * The dependable identity channel for a 2-series chart. A color chip (the mark's
 * color, via `var()` so it flips in dark mode) sits beside a sans text-token
 * label — identity never comes from coloring the text itself.
 */
export default function ChartLegend({ series = SERIES }: ChartLegendProps) {
  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-1.5" aria-hidden="true">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-2">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-sm"
            style={{ backgroundColor: `var(${s.colorVar})` }}
          />
          <span className="text-xs font-medium text-gray-600 dark:text-gray-400">{s.label}</span>
        </li>
      ))}
    </ul>
  );
}
