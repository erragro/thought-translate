import { useState, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

interface Props {
  content: string
  children: ReactNode
  className?: string
}

/** Small hover tooltip, dark-on-light / light-on-dark so it reads
 * against either theme. Shows on hover or keyboard focus of the child. */
export function Tooltip({ content, children, className }: Props) {
  const [visible, setVisible] = useState(false)

  return (
    <span
      className={cn('relative inline-flex', className)}
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
    >
      {children}
      {visible && (
        <span
          role="tooltip"
          className="absolute z-20 bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-max max-w-[220px] rounded-md bg-foreground px-2 py-1.5 text-xs text-surface shadow-lg pointer-events-none"
        >
          {content}
        </span>
      )}
    </span>
  )
}
