import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface ScorePoint {
  name: string;
  score: number;
}

/**
 * Isolated so the charting library can be code-split. It is only reachable from
 * the study detail screen, and the worklist should not download it.
 */
export default function ScoreChart({ data }: { data: ScorePoint[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-20} height={55} interval={0} />
        <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
        <Tooltip />
        <Area
          type="monotone"
          dataKey="score"
          stroke="var(--teal)"
          fill="var(--teal-soft)"
          strokeWidth={2}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
