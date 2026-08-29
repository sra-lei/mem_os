import { Link, NavLink } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ToastProvider } from '../UI/Toast';
import { Badge } from '../UI/Badge';
import { Button } from '../UI/Button';

const NAV: Array<{ to: string; label: string; icon: ReactNode }> = [
  {
    to: '/',
    label: '总览仪表盘',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 13l4-8 5 10 4-6 5 9"/></svg>
    ),
  },
  {
    to: '/runs',
    label: '运行记录',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/></svg>
    ),
  },
  {
    to: '/cases',
    label: '用例定义',
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3h9l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v6h6"/></svg>
    ),
  },
];

/** 整个应用的外层壳：Header + Side Nav + Outlet。 */
export function AppLayout({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <div className="app-shell">
        <header className="app-header">
          <Link to="/" className="app-header__brand">
            <span className="app-header__logo" aria-hidden>
              M
            </span>
            <div className="app-header__titles">
              <strong>MemOS EvalView</strong>
              <span>LLM 评测可视化平台</span>
            </div>
            <Badge tone="info" className="app-header__badge">
              Alpha
            </Badge>
          </Link>
          <div className="app-header__nav">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === '/'}
                className={({ isActive }) =>
                  `app-header__nav-item ${isActive ? 'is-active' : ''}`
                }
              >
                {n.icon}
                <span>{n.label}</span>
              </NavLink>
            ))}
          </div>
          <div className="app-header__actions">
            <Button variant="ghost" size="sm" onClick={() => window.location.reload()}>
              刷新
            </Button>
          </div>
        </header>
        <main className="app-main">
          <div className="container">{children}</div>
        </main>
      </div>
    </ToastProvider>
  );
}
