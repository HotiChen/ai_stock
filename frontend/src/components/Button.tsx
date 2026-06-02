import React from 'react';

type Variant = 'default' | 'primary' | 'danger' | 'ghost';
type Size = 'default' | 'small';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  children: React.ReactNode;
}

const variantStyles: Record<Variant, React.CSSProperties> = {
  default: {
    background: 'var(--surface)',
    color: 'var(--ink)',
    border: '1px solid var(--hair-2)',
  },
  primary: {
    background: 'var(--ink)',
    color: 'var(--surface)',
    border: '1px solid var(--ink)',
  },
  danger: {
    background: 'var(--up)',
    color: '#fff',
    border: '1px solid var(--up)',
  },
  ghost: {
    background: 'transparent',
    color: 'var(--muted)',
    border: 'none',
  },
};

const sizeStyles: Record<Size, React.CSSProperties> = {
  default: {
    padding: '7px 14px',
    fontSize: '13px',
    height: '30px',
  },
  small: {
    padding: '4px 10px',
    fontSize: '12px',
    height: '24px',
  },
};

export default function Button({
  variant = 'default',
  size = 'default',
  children,
  style,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        fontWeight: 500,
        borderRadius: '2px',
        cursor: disabled ? 'not-allowed' : 'pointer',
        fontFamily: 'inherit',
        lineHeight: 1,
        transition: 'opacity 0.1s',
        opacity: disabled ? 0.5 : 1,
        ...variantStyles[variant],
        ...sizeStyles[size],
        ...style,
      }}
      disabled={disabled}
      {...rest}
    >
      {children}
    </button>
  );
}
