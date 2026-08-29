import './registerChart';
import { useMemo } from 'react';
import { Line } from 'react-chartjs-2';
import type { TrendPoint } from '@/types';
import { chartTheme } from './registerChart';
import { versionColor } from '@/utils/color';

/**
 * 版本通过率趋势：按运行开始时间排列 X 轴，颜色按版本散列。
 */
export function TrendLineChart({ data }: { data: TrendPoint[] }) {
  const chartData = useMemo(() => {
    const labels = data.map((p, i) => p.name || `R-${i + 1}`);
    const palette = data.map((p) => versionColor(p.version));
    return {
      labels,
      datasets: [
        {
          type: 'line' as const,
          label: '通过率',
          data: data.map((p) => +(p.pass_rate * 100).toFixed(2)),
          borderColor: chartTheme.brandPrimary(),
          backgroundColor: (() => {
            const c = chartTheme.brandPrimary();
            return c + '22';
          })(),
          borderWidth: 2,
          tension: 0.32,
          pointRadius: 3,
          pointHoverRadius: 6,
          pointBackgroundColor: palette,
          pointBorderColor: '#fff',
          pointBorderWidth: 1.5,
          fill: true,
          yAxisID: 'y',
        },
        {
          type: 'bar' as const,
          label: '用例数',
          data: data.map((p) => p.total_cases),
          backgroundColor: 'rgba(138, 92, 247, 0.18)',
          borderColor: 'rgba(138, 92, 247, 0.42)',
          borderWidth: 1,
          borderRadius: 4,
          yAxisID: 'y1',
        },
      ],
    };
  }, [data]);

  const options = useMemo(
    () => ({
      interaction: { mode: 'index' as const, intersect: false },
      scales: {
        x: {
          grid: { display: false },
          ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10, color: chartTheme.axisColor() },
        },
        y: {
          type: 'linear' as const,
          position: 'left' as const,
          min: 0,
          max: 100,
          grid: { color: chartTheme.gridColor() },
          ticks: { callback: (v: number | string) => `${v}%`, color: chartTheme.axisColor() },
          title: { display: false },
        },
        y1: {
          type: 'linear' as const,
          position: 'right' as const,
          beginAtZero: true,
          grid: { drawOnChartArea: false },
          ticks: { color: chartTheme.axisColor() },
        },
      },
      plugins: {
        legend: { labels: { color: chartTheme.axisColor() } },
        tooltip: {
          callbacks: {
            afterBody: (items: Array<{ dataIndex: number }>) => {
              const point = data[items[0]?.dataIndex];
              if (!point) return '';
              return [
                '',
                `版本：${point.version}`,
                `通过：${point.passed} / 失败：${point.failed}`,
              ];
            },
          },
        },
      },
    }),
    [data],
  );

  return (
    <div className="chart-box chart-box--line">
      <Line data={chartData as any} options={options as any} />
    </div>
  );
}
