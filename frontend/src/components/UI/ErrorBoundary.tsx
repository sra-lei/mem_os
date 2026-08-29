import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Button } from './Button';

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

export class AppErrorBoundary extends Component<Props, State> {
  constructor(p: Props) {
    super(p);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 保留打印，方便生产定位；不引入 logger 避免依赖膨胀
    // eslint-disable-next-line no-console
    console.error('[AppErrorBoundary]', error, info);
  }

  reset = () => {
    this.setState({ error: null });
    if (typeof window !== 'undefined') {
      window.location.hash = '';
      setTimeout(() => window.location.reload(), 30);
    }
  };

  render(): ReactNode {
    const err = this.state.error;
    if (!err) return this.props.children;
    return (
      <div className="error-page">
        <div className="card error-page__card">
          <span className="error-page__code">500</span>
          <h1>页面渲染出错</h1>
          <p className="error-page__msg">{err.message || '未知异常'}</p>
          <pre className="error-page__stack">{err.stack ?? ''}</pre>
          <div className="error-page__actions">
            <Button variant="primary" onClick={this.reset}>
              刷新页面
            </Button>
          </div>
        </div>
      </div>
    );
  }
}
