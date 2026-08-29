import './registerChart';
import { useMemo } from 'react';
import { Bar } from 'react-chartjs-2';
import type { CategoryStat } from '@/types';
import { chartTheme } from './registerChart';
import { PHASE_LABEL } from '@/utils/color';
import { fmtLatency } from '@/utils/format';

/**
 * 分类对比：按阶段展示通过/失败（柱）+ 平均耗时（线）
 * 既可用于仪表盘全局汇总，也可用于单次运行详情。
 */
export function CategoryChart({ data }: { data: CategoryStat[] }) {
  const chartData = useMemo(() => {
    const labels = data.map((c) => PHASE_LABEL[c.phase]);
    return {
      labels,
      datasets: [
        {
          type: 'bar' as const,
          label: '通过',
          data: data.map((c) => c.passed),
          backgroundColor: chartTheme.brandSuccess() + 'bb',
          borderRadius: 4,
          stack: 'cases',
          barPercentage: 0.6,
        },
        {
          type: 'bar' as const,
          label: '失败',
          data: data.map((c) => c.failed),
          backgroundColor: chartTheme.brandDanger() + 'cc',
          borderRadius: 4,
          stack: 'cases',
          barPercentage: 0.6,
        },
        {
          type: 'line' as const,
          label: '平均耗时',
          data: data.map((c) => c.avg_latency_ms),
          borderColor: chartTheme.brandWarning(),
          backgroundColor: chartTheme.brandWarning(),
          yAxisID: 'y1',
          tension: 0.25,
          pointRadius: 4,
        },
      ],
    };
  }, [data]);

  const options = useMemo(
    () => ({
      interaction: { mode: 'index' as const, intersect: false },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { color: chartTheme.axisColor() } },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: chartTheme.gridColor() },
          ticks: { color: chartTheme.axisColor() },
        },
        y1: {
          position: 'right' as const,
          beginAtZero: true,
          grid: { drawOnChartArea: false },
          ticks: { color: chartTheme.axisColor(), callback: (v: number | string) => fmtLatency(Number(v)) },
        },
      },
      plugins: {
        legend: { labels: { color: chartTheme.axisColor() } },
        tooltip: {
          callbacks: {
            footer: (items: Array<{ dataIndex: number }>) => {
              const c = data[items[0]?.dataIndex];
              if (!c) return '';
              return `通过率：${(c.pass_rate * 100).toFixed(1)}%`;
            },
          },
        },
      },
    }),
    [data],
  );

  return (
    <div className="chart-box chart-box--bar">
      <Bar data={chartData as any} options={options as any} />
    </div>
  );
}
