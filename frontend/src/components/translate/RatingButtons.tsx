import { Info, ThumbsDown, ThumbsUp } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Tooltip } from '@/components/ui/Tooltip'
import type { Rating } from '@/api/translate.api'

interface Props {
  rating: Rating | null
  onRate: (rating: Rating) => void
  compact?: boolean
}

const BENEFIT_TEXT =
  'Your rating teaches the system what a good fix looks like. This helps us build a better, more accurate translation tool over time.'

/** The reviewer's signal on a specific fix, feeding the correction corpus
 * (§5/§6) — deliberately real buttons (border, fill, label), not bare
 * icons, since this is the actual decision point in the workflow: "good"
 * resolves the comment, "needs work" leaves it open for another comment. */
export function RatingButtons({ rating, onRate, compact = false }: Props) {
  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={() => onRate('up')}
        aria-label="Good fix, resolve this comment"
        className={cn(
          'inline-flex items-center gap-1.5 rounded-md border font-medium transition-colors',
          compact ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-sm',
          rating === 'up'
            ? 'border-green-300 bg-green-50 text-green-700 dark:border-green-500/40 dark:bg-green-500/15 dark:text-green-400'
            : 'border-surface-border text-muted hover:bg-surface hover:text-foreground'
        )}
      >
        <ThumbsUp className={compact ? 'w-3.5 h-3.5' : 'w-4 h-4'} />
        Good fix
      </button>
      <button
        type="button"
        onClick={() => onRate('down')}
        aria-label="Needs work, keep this comment open"
        className={cn(
          'inline-flex items-center gap-1.5 rounded-md border font-medium transition-colors',
          compact ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-sm',
          rating === 'down'
            ? 'border-red-300 bg-red-50 text-red-700 dark:border-red-500/40 dark:bg-red-500/15 dark:text-red-400'
            : 'border-surface-border text-muted hover:bg-surface hover:text-foreground'
        )}
      >
        <ThumbsDown className={compact ? 'w-3.5 h-3.5' : 'w-4 h-4'} />
        Needs work
      </button>
      <Tooltip content={BENEFIT_TEXT}>
        <button type="button" aria-label="Why does rating matter?" className="text-subtle hover:text-foreground">
          <Info className={compact ? 'w-3.5 h-3.5' : 'w-4 h-4'} />
        </button>
      </Tooltip>
    </div>
  )
}
