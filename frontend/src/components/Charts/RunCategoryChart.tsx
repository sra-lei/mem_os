import './registerChart';
import { useMemo } from 'react';
import { Bar } from 'react-chartjs-2';
import type { CategoryStat } from '@/types';
import { chartTheme } from './registerChart';
import { PHASE_LABEL } from '@/utils/color';
import { fmtLatency } from '@/utils/format';

/** 运行详情 → 分类通过率 + 耗时双轴（与 CategoryChart 类似但更强调每阶段通过率的百分比展示） */
export function RunCategoryChart({ data }: { data: CategoryStat[] }) {
  const chartData = useMemo(() => {
    const labels = data.map((c) => PHASE_LABEL[c.phase]);
    return {
      labels,
      datasets: [
        {
          type: 'bar' as const,
          label: '通过率',
          data: data.map((c) => +(c.pass_rate * 100).toFixed(1)),
          backgroundColor: data.map((c) => {
            const p = c.pass_rate;
            if (p >= 0.9) return chartTheme.brandSuccess() + 'cc';
            if (p >= 0.7) return chartTheme.brandWarning() + 'cc';
            return chartTheme.brandDanger() + 'cc';
          }),
          yAxisID: 'y',
          borderRadius: 6,
          barPercentage: 0.48,
        },
        {
          type: 'line' as const,
          label: '平均耗时',
          data: data.map((c) => c.avg_latency_ms),
          borderColor: chartTheme.brandPrimary(),
          backgroundColor: chartTheme.brandPrimary(),
          yAxisID: 'y1',
          pointRadius: 4,
          tension: 0.2,
        },
      ],
    };
  }, [data]);

  const options = useMemo(
    () => ({
      interaction: { mode: 'index' as const, intersect: false },
      scales: {
        x: { grid: { display: false }, ticks: { color: chartTheme.axisColor() } },
        y: {
          position: 'left' as const,
          min: 0,
          max: 100,
          grid: { color: chartTheme.gridColor() },
          ticks: { callback: (v: number | string) => `${v}%`, color: chartTheme.axisColor() },
        },
        y1: {
          position: 'right' as const,
          beginAtZero: true,
          grid: { drawOnChartArea: false },
          ticks: { callback: (v: number | string) => fmtLatency(Number(v)), color: chartTheme.axisColor() },
        },
      },
      plugins: {
        legend: { labels: { color: chartTheme.axisColor() } },
      },
    }),
    [],
  );

  return (
    <div className="chart-box chart-box--bar">
      <Bar data={chartData as any} options={options as any} />
    </div>
  );
}
