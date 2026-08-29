import './registerChart';
import { useMemo } from 'react';
import { Pie } from 'react-chartjs-2';
import { chartTheme } from './registerChart';
import type { RunSummary } from '@/types';

export function PassFailPieChart({ run }: { run: RunSummary | null }) {
  const passed = run?.passed ?? 0;
  const failed = run?.failed ?? 0;
  const skipped = Math.max(0, (run?.total_cases ?? 0) - passed - failed);

  const data = useMemo(() => {
    const labels = ['通过', '失败'];
    const vals = [passed, failed];
    const colors = [chartTheme.brandSuccess(), chartTheme.brandDanger()];
    if (skipped > 0) {
      labels.push('跳过');
      vals.push(skipped);
      colors.push('#9aa4b8');
    }
    return {
      labels,
      datasets: [
        {
          data: vals,
          backgroundColor: colors,
          borderColor: '#fff',
          borderWidth: 2,
          hoverOffset: 6,
        },
      ],
    };
  }, [passed, failed, skipped]);

  const options = useMemo(
    () => ({
      plugins: {
        legend: { position: 'bottom' as const, labels: { color: chartTheme.axisColor() } },
        tooltip: {
          callbacks: {
            label: (item: { label?: string; parsed: number | number[] }) => {
              const v = Array.isArray(item.parsed) ? item.parsed[0] : item.parsed;
              const total = passed + failed + skipped || 1;
              return ` ${item.label ?? ''}：${v}（${((v / total) * 100).toFixed(1)}%）`;
            },
          },
        },
      },
      cutout: '64%',
    }),
    [passed, failed, skipped],
  );

  return (
    <div className="chart-box chart-box--pie">
      <Pie data={data} options={options} />
    </div>
  );
}
