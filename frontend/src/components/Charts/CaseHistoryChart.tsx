import './registerChart';
import { useMemo } from 'react';
import { Scatter } from 'react-chartjs-2';
import type { CaseHistoryItem } from '@/types';
import { chartTheme } from './registerChart';

/** 用例详情历史散点图：X=时间顺序, Y=耗时, 颜色=通过/失败 */
export function CaseHistoryChart({ items }: { items: CaseHistoryItem[] }) {
  const data = useMemo(() => {
    const success = items
      .filter((i) => i.passed)
      .map((i, idx) => ({ x: idx + 1, y: i.latency_ms || 0, id: i.id }));
    const fail = items
      .filter((i) => !i.passed)
      .map((i, idx) => ({ x: idx + 1, y: i.latency_ms || 0, id: i.id }));
    return {
      datasets: [
        {
          label: '通过',
          data: success as unknown as { x: number; y: number }[],
          backgroundColor: chartTheme.brandSuccess(),
          pointRadius: 6,
          pointHoverRadius: 8,
          pointBorderColor: '#fff',
          pointBorderWidth: 1.5,
        },
        {
          label: '失败',
          data: fail as unknown as { x: number; y: number }[],
          backgroundColor: chartTheme.brandDanger(),
          pointRadius: 7,
          pointHoverRadius: 9,
          pointStyle: 'triangle' as const,
          pointBorderColor: '#fff',
          pointBorderWidth: 1.5,
        },
      ],
    };
  }, [items]);

  const options = useMemo(
    () => ({
      scales: {
        x: {
          title: { display: true, text: '运行顺序（历史 → 最新）', color: chartTheme.axisColor() },
          grid: { color: chartTheme.gridColor() },
          ticks: { color: chartTheme.axisColor(), stepSize: 1 },
        },
        y: {
          title: { display: true, text: '耗时 (ms)', color: chartTheme.axisColor() },
          grid: { color: chartTheme.gridColor() },
          ticks: { color: chartTheme.axisColor() },
          beginAtZero: true,
        },
      },
      plugins: {
        legend: { labels: { color: chartTheme.axisColor() } },
      },
    }),
    [],
  );

  if (items.length === 0) {
    return (
      <div className="chart-empty">暂无历史运行数据</div>
    );
  }

  return (
    <div className="chart-box chart-box--scatter">
      <Scatter data={data} options={options} />
    </div>
  );
}
