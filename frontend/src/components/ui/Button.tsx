import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Link, type LinkProps } from 'react-router'
import { IconSpinner } from './icons'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'quiet' | 'dark'
export type ButtonSize = 'sm' | 'md' | 'lg'

const base =
  'inline-flex items-center justify-center gap-2 rounded-md font-medium whitespace-nowrap select-none transition-[background-color,border-color,color,box-shadow] duration-150 ease-out disabled:cursor-not-allowed aria-disabled:cursor-not-allowed'

const variants: Record<ButtonVariant, string> = {
  primary:
    'bg-cobalt text-white shadow-[0_1px_0_rgb(255_255_255/0.15)_inset,0_1px_2px_rgb(11_18_32/0.12)] hover:bg-cobalt-700 active:bg-cobalt-800 disabled:bg-line-strong disabled:text-white aria-disabled:bg-line-strong',
  secondary: 'bg-canvas text-ink border border-line-strong hover:border-ink-2/40 hover:bg-mist active:bg-mist-2 disabled:text-faint disabled:bg-mist',
  ghost: 'bg-transparent text-ink hover:bg-mist-2 active:bg-line disabled:text-faint',
  quiet: 'bg-transparent text-cobalt hover:bg-cobalt-50 active:bg-cobalt-100 disabled:text-faint',
  danger: 'bg-canvas text-danger border border-danger-200 hover:bg-danger-50 active:bg-danger-50 disabled:text-faint disabled:border-line',
  dark: 'bg-ink text-white shadow-[0_1px_2px_rgb(11_18_32/0.18)] hover:bg-ink-2 active:bg-ink disabled:bg-line-strong',
}

// Every size keeps a >=44px target on touch screens; 'sm' tightens to 36px only for fine (mouse) pointers.
const sizes: Record<ButtonSize, string> = {
  sm: 'min-h-11 px-3 text-sm pointer-fine:min-h-9',
  md: 'min-h-11 px-4 text-[0.95rem]',
  lg: 'min-h-12 px-5 text-base',
}

function buttonClass(variant: ButtonVariant = 'primary', size: ButtonSize = 'md', extra = ''): string {
  return `${base} ${variants[variant]} ${sizes[size]} ${extra}`
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
  iconRight?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading = false, icon, iconRight, className = '', children, disabled, type = 'button', ...rest },
  ref,
) {
  return (
    <button ref={ref} type={type} className={buttonClass(variant, size, className)} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <IconSpinner /> : icon}
      {children}
      {iconRight}
    </button>
  )
})

export interface ButtonLinkProps extends LinkProps {
  variant?: ButtonVariant
  size?: ButtonSize
  icon?: ReactNode
  iconRight?: ReactNode
}

export function ButtonLink({ variant = 'primary', size = 'md', icon, iconRight, className = '', children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={buttonClass(variant, size, className)} {...rest}>
      {icon}
      {children}
      {iconRight}
    </Link>
  )
}

export function AnchorButton({
  variant = 'secondary',
  size = 'md',
  icon,
  className = '',
  children,
  ...rest
}: React.AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: ButtonVariant; size?: ButtonSize; icon?: ReactNode }) {
  return (
    <a className={buttonClass(variant, size, className)} {...rest}>
      {icon}
      {children}
    </a>
  )
}
