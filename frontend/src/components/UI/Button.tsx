import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'link';
type Size = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  loading?: boolean;
  block?: boolean;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  leftIcon,
  rightIcon,
  loading = false,
  block = false,
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={[
        'btn',
        `btn--${variant}`,
        `btn--${size}`,
        block ? 'btn--block' : '',
        loading ? 'btn--loading' : '',
        className ?? '',
      ]
        .filter(Boolean)
        .join(' ')}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? (
        <span className="btn__spinner" aria-hidden />
      ) : (
        leftIcon ? <span className="btn__icon">{leftIcon}</span> : null
      )}
      <span className="btn__label">{children}</span>
      {!loading && rightIcon ? <span className="btn__icon">{rightIcon}</span> : null}
    </button>
  );
}
