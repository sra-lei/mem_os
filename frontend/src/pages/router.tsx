import { Link } from 'react-router-dom';
import { Button } from '@/components/UI';
import type { RouteObject } from 'react-router-dom';
import { AppLayout } from '@/components/Layout';
import { DashboardPage } from './DashboardPage';
import { RunsPage } from './RunsPage';
import { RunDetailPage } from './RunDetailPage';
import { CasesPage } from './CasesPage';
import { CaseDetailPage } from './CaseDetailPage';
import { CaseHistoryPage } from './CaseHistoryPage';

export function ErrorPage({ code = 404, title = '页面不存在' }: { code?: number; title?: string }) {
  return (
    <div className="error-page">
      <div className="card error-page__card">
        <span className="error-page__code">{code}</span>
        <h1>{title}</h1>
        <p className="error-page__msg">
          找不到您要访问的页面，可能链接有误或内容已被移除。
        </p>
        <div className="error-page__actions">
          <Link to="/">
            <Button variant="primary">返回仪表盘</Button>
          </Link>
          <Link to="/runs">
            <Button variant="secondary">查看运行列表</Button>
          </Link>
        </div>
      </div>
    </div>
  );
}

export const routes: RouteObject[] = [
  {
    path: '/',
    element: (
      <AppLayout>
        <DashboardPage />
      </AppLayout>
    ),
  },
  {
    path: '/runs',
    element: (
      <AppLayout>
        <RunsPage />
      </AppLayout>
    ),
  },
  {
    path: '/runs/:runId',
    element: (
      <AppLayout>
        <RunDetailPage />
      </AppLayout>
    ),
  },
  {
    path: '/cases',
    element: (
      <AppLayout>
        <CasesPage />
      </AppLayout>
    ),
  },
  {
    path: '/cases/:caseId',
    element: (
      <AppLayout>
        <CaseDetailPage />
      </AppLayout>
    ),
  },
  {
    path: '/cases/:caseId/history',
    element: (
      <AppLayout>
        <CaseHistoryPage />
      </AppLayout>
    ),
  },
  {
    path: '*',
    element: (
      <AppLayout>
        <ErrorPage />
      </AppLayout>
    ),
  },
];
