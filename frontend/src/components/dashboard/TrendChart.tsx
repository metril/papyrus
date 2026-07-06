import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { LabelList } from 'recharts';
import type { TrendPoint } from '../../api/admin';
import { useChartColors } from './chartTheme';
import ChartLegend from './ChartLegend';
import ChartTooltip from './ChartTooltip';
import ChartDataTable from './ChartDataTable';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `"2026-06-07"` → `"06-07"` (axis ticks, TZ-safe string slice). */
function formatAxisDate(value: string | number): string {
  return String(value).slice(5);
}

/** `"2026-06-07"` → `"Jun 7"` (tooltip heading, TZ-safe). */
function formatFullDate(value: string | number): string {
  const parts = String(value).split('-');
  if (parts.length !== 3) return String(value);
  const month = MONTHS[Number(parts[1]) - 1] ?? parts[1];
  return `${month} ${Number(parts[2])}`;
}

interface EndLabelProps {
  x?: number | string;
  y?: number | string;
  index?: number;
}

/**
 * Direct label riding the right end of a line: a color chip (the series color)
 * beside a sans text-token word — never the value in the series color. Rendered
 * for the last point only; the two series get opposite vertical offsets so they
 * never collide (even when both sit on the zero-line).
 */
function makeEndLabel(text: string, chipFill: string, textFill: string, lastIndex: number, dy: number) {
  return function EndLabel({ x, y, index }: EndLabelProps) {
    const nx = Number(x);
    const ny = Number(y);
    if (index !== lastIndex || !Number.isFinite(nx) || !Number.isFinite(ny)) return null;
    const cy = ny + dy;
    return (
      <g>
        <circle cx={nx + 10} cy={cy} r={4} fill={chipFill} />
        <text x={nx + 18} y={cy} dominantBaseline="central" fontSize={11} fill={textFill}>
          {text}
        </text>
      </g>
    );
  };
}

interface TrendChartProps {
  data: TrendPoint[];
}

/**
 * 30-day print/scan trend. Two lines (print = cyan, scan = magenta), a recessive
 * horizontal grid, one y-axis, a legend, per-line direct end-labels, and a
 * crosshair tooltip. All-zero data is honest — the lines simply sit on the
 * zero-line rather than the card going blank.
 */
export default function TrendChart({ data }: TrendChartProps) {
  const colors = useChartColors();
  const lastIndex = data.length - 1;

  return (
    <div>
      <ChartLegend />
      <div className="mt-3 h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 12, right: 60, bottom: 4, left: 4 }}>
            <CartesianGrid vertical={false} stroke={colors.grid} strokeWidth={1} />
            <XAxis
              dataKey="date"
              tickFormatter={formatAxisDate}
              interval={6}
              tick={{ fontSize: 11, fill: colors.axis }}
              tickLine={false}
              axisLine={{ stroke: colors.grid }}
              minTickGap={8}
            />
            <YAxis
              allowDecimals={false}
              width={32}
              tick={{ fontSize: 11, fill: colors.axis }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ stroke: colors.grid, strokeWidth: 1 }}
              content={<ChartTooltip labelFormatter={formatFullDate} />}
            />
            <Line
              type="monotone"
              dataKey="prints"
              stroke={colors.print}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            >
              <LabelList content={makeEndLabel('Prints', colors.print, colors.label, lastIndex, -9)} />
            </Line>
            <Line
              type="monotone"
              dataKey="scans"
              stroke={colors.scan}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            >
              <LabelList content={makeEndLabel('Scans', colors.scan, colors.label, lastIndex, 9)} />
            </Line>
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ChartDataTable
        caption="Prints and scans per day over the last 30 days"
        headers={['Date', 'Prints', 'Scans']}
        rows={data.map((d) => [d.date, d.prints, d.scans])}
      />
    </div>
  );
}
