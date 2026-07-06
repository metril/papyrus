import { SERIES } from './chartTheme';

interface TooltipPayloadItem {
  dataKey?: string | number;
  value?: number | string;
}

interface ChartTooltipProps {
  /** Injected by Recharts' <Tooltip content={...}>. */
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: string | number;
  /** Formats the category/x value into the tooltip heading. */
  labelFormatter?: (label: string | number) => string;
}

/**
 * Crosshair/hover tooltip styled as a small Card: heading (the date or user, in
 * sans — it's a label, not a numeral), then one row per series — a color chip
 * (via `var()`, flips in dark mode) plus a text-token name and a `font-mono`
 * value. Text never wears the series color; only the numerals are mono.
 */
export default function ChartTooltip({ active, payload, label, labelFormatter }: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;

  const heading = labelFormatter && label != null ? labelFormatter(label) : String(label ?? '');

  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-md dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{heading}</div>
      <ul className="space-y-0.5">
        {SERIES.map((s) => {
          const item = payload.find((p) => p.dataKey === s.key);
          if (!item) return null;
          return (
            <li key={s.key} className="flex items-center gap-2">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-sm"
                style={{ backgroundColor: `var(${s.colorVar})` }}
              />
              <span className="text-xs text-gray-600 dark:text-gray-400">{s.label}</span>
              <span className="ml-auto pl-3 font-mono text-xs font-medium text-gray-900 dark:text-gray-100">
                {item.value ?? 0}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
