import { useEffect, useState } from 'react';

/**
 * Chart series identity — color follows the entity, never its position:
 * print is ALWAYS process-cyan, scan is ALWAYS process-magenta, in every chart.
 * The CSS var names resolve to the validated palette in `index.css` and flip in
 * dark mode. HTML marks use `var()` directly; SVG marks read the resolved hex
 * from `useChartColors()`.
 */
export const PRINT_VAR = '--chart-print';
export const SCAN_VAR = '--chart-scan';

export interface ChartSeries {
  /** Data key on the trend/user rows. */
  key: 'prints' | 'scans';
  /** Human label (sans, text token — never the series color). */
  label: string;
  /** CSS custom property carrying this series' color. */
  colorVar: string;
}

export const SERIES: readonly ChartSeries[] = [
  { key: 'prints', label: 'Prints', colorVar: PRINT_VAR },
  { key: 'scans', label: 'Scans', colorVar: SCAN_VAR },
] as const;

export interface ChartColors {
  /** Resolved hex for the print series (process cyan). */
  print: string;
  /** Resolved hex for the scan series (process magenta). */
  scan: string;
  /** Recessive gridline color (gray-200 light / gray-800 dark). */
  grid: string;
  /** Axis tick text (gray-500, legible on both surfaces). */
  axis: string;
  /** Direct-label / value text token (gray-700 light / gray-300 dark). */
  label: string;
}

const EMPTY: ChartColors = { print: '', scan: '', grid: '', axis: '', label: '' };

function readChartColors(): ChartColors {
  if (typeof window === 'undefined' || typeof document === 'undefined') return EMPTY;
  const root = document.documentElement;
  const cs = getComputedStyle(root);
  const isDark = root.classList.contains('dark');
  const v = (name: string) => cs.getPropertyValue(name).trim();
  return {
    print: v(PRINT_VAR),
    scan: v(SCAN_VAR),
    grid: v(isDark ? '--color-gray-800' : '--color-gray-200'),
    axis: v('--color-gray-500'),
    label: v(isDark ? '--color-gray-300' : '--color-gray-700'),
  };
}

/**
 * Resolve the chart palette to concrete hex for SVG marks (Recharts sets
 * `stroke`/`fill` as SVG presentation attributes, where `var()` does not
 * resolve). Re-reads whenever the `.dark` class on <html> flips so dark mode is
 * a genuine repaint from the dark palette, not a stale light one.
 */
export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(readChartColors);

  useEffect(() => {
    const root = document.documentElement;
    // The useState initializer already read the current palette during render;
    // this observer repaints only when the `.dark` class on <html> flips, so the
    // dark palette is a genuine re-read rather than a stale light one.
    const observer = new MutationObserver(() => setColors(readChartColors()));
    observer.observe(root, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  return colors;
}
