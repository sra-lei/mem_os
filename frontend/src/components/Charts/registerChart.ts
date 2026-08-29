/**
 * Chart.js 全局默认注册 + 主题化。
 * 所有图表组件均从此 import 一次，确保注册完成。
 */
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Legend,
  Tooltip,
  Filler,
  Title,
} from 'chart.js';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Legend,
  Tooltip,
  Filler,
  Title,
);

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

if (ChartJS.defaults.animation !== false) {
  ChartJS.defaults.animation.duration = 500;
}
ChartJS.defaults.font.family = '"Inter", "PingFang SC", system-ui, sans-serif';
ChartJS.defaults.color = () => cssVar('--color-text-secondary', '#5a667a');
ChartJS.defaults.borderColor = () => cssVar('--color-border-subtle', '#eef0f5');
ChartJS.defaults.plugins.legend.labels.usePointStyle = true;
ChartJS.defaults.plugins.legend.labels.boxWidth = 8;
ChartJS.defaults.plugins.legend.labels.boxHeight = 8;
ChartJS.defaults.plugins.tooltip.backgroundColor = () => cssVar('--color-surface-inverse', '#1a2132');
ChartJS.defaults.plugins.tooltip.titleColor = '#fff';
ChartJS.defaults.plugins.tooltip.bodyColor = '#dde3ef';
ChartJS.defaults.plugins.tooltip.borderColor = 'rgba(255,255,255,0.06)';
ChartJS.defaults.plugins.tooltip.borderWidth = 1;
ChartJS.defaults.plugins.tooltip.cornerRadius = 8;
ChartJS.defaults.plugins.tooltip.padding = 10;
ChartJS.defaults.maintainAspectRatio = false;
ChartJS.defaults.plugins.legend.position = 'bottom';

export const chartTheme = {
  gridColor: () => cssVar('--color-border-subtle', '#eef0f5'),
  axisColor: () => cssVar('--color-text-tertiary', '#8b94a9'),
  brandPrimary: () => cssVar('--color-brand-primary', '#4a6cf7'),
  brandSuccess: () => cssVar('--color-success', '#12b76a'),
  brandWarning: () => cssVar('--color-warning', '#f79009'),
  brandDanger: () => cssVar('--color-danger', '#d92d20'),
  phaseMemory: () => cssVar('--color-phase-memory', '#8a5cf7'),
  phasePrompt: () => cssVar('--color-phase-prompt', '#0ea5e9'),
  phaseAnswer: () => cssVar('--color-phase-answer', '#f79009'),
  phaseAll: () => cssVar('--color-phase-all', '#4a6cf7'),
  phaseColor(phase: 'memory_retrieval' | 'prompt_generation' | 'answer_generation' | 'all' | null | undefined): string {
    switch (phase) {
      case 'memory_retrieval':
        return this.phaseMemory();
      case 'prompt_generation':
        return this.phasePrompt();
      case 'answer_generation':
        return this.phaseAnswer();
      case 'all':
      default:
        return this.phaseAll();
    }
  },
};
