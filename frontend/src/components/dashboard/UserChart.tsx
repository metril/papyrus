import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Users } from 'lucide-react';
import type { UserUsage } from '../../api/admin';
import EmptyState from '../common/EmptyState';
import { useChartColors } from './chartTheme';
import ChartLegend from './ChartLegend';
import ChartTooltip from './ChartTooltip';
import ChartDataTable from './ChartDataTable';

/** Label the value at every bar end only when the field is small enough to stay
 * uncluttered; past this, label just the larger bar of each user's pair. */
const LABEL_ALL_MAX_USERS = 6;

interface BarLabelProps {
  x?: number | string;
  y?: number | string;
  width?: number | string;
  height?: number | string;
  index?: number;
}

/**
 * Value at a horizontal bar's tip, in `font-mono` text-token color (never the
 * series color). Selective: labels every bar when the user count is small,
 * otherwise only the larger bar of each pair — `preferOnTie` keeps ties from
 * double-labeling by awarding the tie to prints alone.
 */
function makeBarLabel(
  seriesKey: 'prints' | 'scans',
  data: UserUsage[],
  showAll: boolean,
  preferOnTie: boolean,
  fill: string,
) {
  return function BarLabel({ x, y, width, height, index }: BarLabelProps) {
    const nx = Number(x);
    const ny = Number(y);
    const nw = Number(width);
    const nh = Number(height);
    if (index == null || ![nx, ny, nw, nh].every(Number.isFinite)) return null;
    const row = data[index];
    if (!row) return null;
    const value = row[seriesKey];
    const other = seriesKey === 'prints' ? row.scans : row.prints;
    if (value <= 0) return null;
    if (!showAll && (preferOnTie ? value < other : value <= other)) return null;
    return (
      <text
        x={nx + nw + 4}
        y={ny + nh / 2}
        dominantBaseline="central"
        textAnchor="start"
        fontSize={11}
        className="font-mono"
        fill={fill}
      >
        {value}
      </text>
    );
  };
}

interface UserChartProps {
  data: UserUsage[];
}

/**
 * Per-user print/scan totals as a horizontal grouped bar chart (print = cyan,
 * scan = magenta). Thin bars with a rounded data-end, a legend, selective mono
 * value labels, and a hover tooltip. Empty data yields an EmptyState rather than
 * an empty plot.
 */
export default function UserChart({ data }: UserChartProps) {
  const colors = useChartColors();

  if (data.length === 0) {
    return <EmptyState icon={Users} title="No activity yet" hint="Print or scan a document to see per-user usage here." />;
  }

  const showAll = data.length <= LABEL_ALL_MAX_USERS;
  const height = Math.max(160, data.length * 36);

  return (
    <div>
      <ChartLegend />
      <div className="mt-3" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 40, bottom: 4, left: 4 }}
            barGap={2}
            barCategoryGap="28%"
          >
            <XAxis type="number" hide allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="username"
              width={84}
              tick={{ fontSize: 12, fill: colors.axis }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: colors.grid, fillOpacity: 0.15 }}
              content={<ChartTooltip labelFormatter={(l) => String(l)} />}
            />
            <Bar dataKey="prints" fill={colors.print} barSize={10} radius={[0, 4, 4, 0]} isAnimationActive={false}>
              <LabelList content={makeBarLabel('prints', data, showAll, true, colors.label)} />
            </Bar>
            <Bar dataKey="scans" fill={colors.scan} barSize={10} radius={[0, 4, 4, 0]} isAnimationActive={false}>
              <LabelList content={makeBarLabel('scans', data, showAll, false, colors.label)} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartDataTable
        caption="Print and scan totals per user"
        headers={['User', 'Prints', 'Scans']}
        rows={data.map((u) => [u.username, u.prints, u.scans])}
      />
    </div>
  );
}
