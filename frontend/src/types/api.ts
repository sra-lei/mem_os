// 通用 API 响应类型 + 错误类型

export interface ApiErrorBody {
  detail?:
    | string
    | Array<{
        loc?: (string | number)[];
        msg?: string;
        type?: string;
      }>;
}

export class ApiError extends Error {
  public readonly status: number;
  public readonly url: string;
  public readonly body: ApiErrorBody;

  constructor(status: number, url: string, body: ApiErrorBody, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.url = url;
    this.body = body;
  }

  /** 转为面向用户的简洁提示 */
  public toUserMessage(): string {
    if (typeof this.body.detail === 'string') return this.body.detail;
    if (Array.isArray(this.body.detail) && this.body.detail.length > 0) {
      const first = this.body.detail[0];
      const loc = first.loc ? `[${first.loc.join('.')}] ` : '';
      return `${loc}${first.msg ?? this.message}`;
    }
    return this.message || `请求失败（HTTP ${this.status}）`;
  }
}
