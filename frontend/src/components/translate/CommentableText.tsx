/**
 * CommentableText.tsx
 *
 * The span-select, inline-comment editor from CONCEPT.md §3 — "select
 * text -> anchored comment -> resolve/reply thread", Google-Docs-style.
 *
 * Comments are fetched per THREAD (every version), not per version — a
 * comment stays visible after it triggers a revision instead of vanishing
 * the moment the displayed version moves forward (comments live on the
 * version they were created on; the thread keeps moving to a new latest
 * version on every revision). Each comment carries its own resulting
 * correction (mt_output/corrected_output/rating) once one exists, so the
 * diff + thumbs sit right on the comment that caused them — supporting
 * multi-turn refinement (leave another comment on the same spot) instead
 * of a one-shot verdict.
 *
 * Two trigger modes, matching how paste vs. upload actually differ:
 *   - autoRevise=true  (paste mode, default): each comment immediately
 *     triggers a revision.
 *   - autoRevise=false (document/upload mode): comments accumulate
 *     silently (onCommentAdded fires so the parent can count them); the
 *     parent's "Regenerate with N comments" button triggers the batch
 *     revision instead, since a document has many chunks and retrigger-
 *     ing per comment would mean many redundant Sarvam calls.
 *
 * Character offsets are computed relative to the container's rendered
 * text (not the raw prop string) via Range.toString().length, which is
 * robust to the highlighted <mark> spans this component itself renders.
 * Inline highlighting only applies to comments left on the CURRENTLY
 * displayed version — an older comment's span offsets aren't guaranteed
 * valid against newer text, so it shows in the list (with its own diff)
 * but isn't marked inline.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'
import { cn } from '@/lib/cn'
import { translateApi, type Comment, type NewVersion, type Rating } from '@/api/translate.api'
import { RatingButtons } from './RatingButtons'

interface Props {
  threadId: number
  versionId: number
  text: string
  /** Historical (non-latest) versions are display-only — no new comments,
   * no resolve/rate/delete. Comments already on the thread still show. */
  readOnly?: boolean
  /** Fires when a comment triggers a revision (paste mode) — the parent
   * jumps to displaying the new version. */
  onRevised?: (newVersion: NewVersion) => void
  /** If true (default), each comment immediately triggers a revision.
   * If false, comments just accumulate — pass onCommentAdded to track
   * them for a batch "regenerate" action instead. */
  autoRevise?: boolean
  onCommentAdded?: (comment: Comment) => void
  /** Which chunk (paragraph index) this text belongs to, for document
   * mode — stored with the comment so batch regeneration knows which
   * chunk each piece of feedback applies to. */
  chunkIndex?: number
  /** Renders this block as a heading (larger, semibold) — document mode,
   * from document_parser.py's heading detection (font-size heuristic for
   * PDF, paragraph style for DOCX). */
  heading?: boolean
  /** Tighter text + comment-card sizing for use inside a spreadsheet cell,
   * where a paragraph-sized comment card would blow out the table layout. */
  compact?: boolean
}

interface PendingSelection {
  start: number
  end: number
  quoted: string
  x: number
  y: number
  /** 'above' opens the popover above the selection (default); 'below' when
   * there isn't enough room above — e.g. a selection on the container's
   * first line — so it doesn't render past the top of the page. */
  placement: 'above' | 'below'
}

// Rough rendered height of the comment popover (category select + textarea
// + buttons) — used to decide whether there's room to open it above the
// selection or it needs to flip below instead.
const POPOVER_HEIGHT_ESTIMATE = 160

const CATEGORIES = ['accuracy', 'fluency', 'terminology', 'style'] as const
type Category = (typeof CATEGORIES)[number]

function getOffset(container: Node, node: Node, offset: number): number {
  const range = document.createRange()
  range.selectNodeContents(container)
  range.setEnd(node, offset)
  return range.toString().length
}

export function CommentableText({
  threadId,
  versionId,
  text,
  readOnly = false,
  onRevised,
  autoRevise = true,
  onCommentAdded,
  chunkIndex,
  heading = false,
  compact = false,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const [comments, setComments] = useState<Comment[]>([])
  const [pending, setPending] = useState<PendingSelection | null>(null)
  const [commentText, setCommentText] = useState('')
  const [category, setCategory] = useState<Category>('accuracy')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setComments([])
    translateApi
      .listThreadComments(threadId)
      .then((res) => {
        // Document mode: several paragraphs share one thread, so this
        // fetch returns every paragraph's comments — keep only this
        // paragraph's own (chunkIndex undefined in paste mode, where
        // every comment on the thread already belongs to this text).
        const mine =
          chunkIndex === undefined ? res.data : res.data.filter((c) => c.chunk_index === chunkIndex)
        setComments(mine)
      })
      .catch(() => {})
    // versionId isn't used in the request (thread-wide, not per-version) —
    // it's here purely so a regenerate/revise (which creates a new version
    // for the same thread/chunk) triggers a refetch and picks up the
    // correction that call just produced, without depending on the parent
    // remounting this component via a version-keyed key.
  }, [threadId, chunkIndex, versionId])

  // Dismiss the popover on any click outside it — mousedown so it fires
  // before the click that opened a *different* selection's popover, and
  // only attached while a popover is actually open.
  useEffect(() => {
    if (!pending) return
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setPending(null)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [pending])

  const handleMouseUp = useCallback(() => {
    if (readOnly) return
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !containerRef.current) return
    if (!containerRef.current.contains(sel.anchorNode) || !containerRef.current.contains(sel.focusNode)) return

    const range = sel.getRangeAt(0)
    const quoted = sel.toString()
    if (!quoted.trim()) return

    const start = getOffset(containerRef.current, range.startContainer, range.startOffset)
    const end = getOffset(containerRef.current, range.endContainer, range.endOffset)
    const rect = range.getBoundingClientRect()
    const containerRect = containerRef.current.getBoundingClientRect()

    // Popover is w-64 (256px) and centered via -translate-x-1/2 — clamp so
    // it can't render off either edge of the container.
    const POPOVER_HALF_WIDTH = 128
    const rawX = rect.left - containerRect.left + rect.width / 2
    const clampedX = Math.min(
      Math.max(rawX, POPOVER_HALF_WIDTH),
      Math.max(containerRect.width - POPOVER_HALF_WIDTH, POPOVER_HALF_WIDTH)
    )

    const anchorTop = rect.top - containerRect.top
    const anchorBottom = rect.bottom - containerRect.top
    const placement: 'above' | 'below' = anchorTop >= POPOVER_HEIGHT_ESTIMATE ? 'above' : 'below'

    setPending({
      start: Math.min(start, end),
      end: Math.max(start, end),
      quoted,
      x: clampedX,
      y: Math.max(placement === 'above' ? anchorTop : anchorBottom, 0),
      placement,
    })
    setCommentText('')
    setCategory('accuracy')
  }, [readOnly])

  const handleSaveComment = async () => {
    if (!pending || !commentText.trim()) return
    setSaving(true)
    try {
      const res = await translateApi.addComment(versionId, {
        span_start: pending.start,
        span_end: pending.end,
        quoted_text: pending.quoted,
        comment_text: commentText.trim(),
        category,
        chunk_index: chunkIndex,
        auto_revise: autoRevise,
      })
      setComments((prev) => [...prev, res.data.comment].sort((a, b) => a.span_start - b.span_start))
      setPending(null)
      window.getSelection()?.removeAllRanges()
      if (autoRevise && res.data.new_version) {
        onRevised?.(res.data.new_version)
      } else if (!autoRevise) {
        onCommentAdded?.(res.data.comment)
      }
    } finally {
      setSaving(false)
    }
  }

  const handleResolve = async (id: number) => {
    const res = await translateApi.resolveComment(id)
    setComments((prev) => prev.map((c) => (c.id === id ? res.data : c)))
  }

  const handleDelete = async (id: number) => {
    await translateApi.deleteComment(id)
    setComments((prev) => prev.filter((c) => c.id !== id))
  }

  const handleRate = async (id: number, rating: Rating) => {
    // Optimistic: 'up' resolves (the card disappears from the open list),
    // 'down' just records the rating and leaves the comment open so
    // another comment can refine the same spot further.
    setComments((prev) => prev.map((c) => (c.id === id ? { ...c, rating, status: rating === 'up' ? 'resolved' : c.status } : c)))
    try {
      await translateApi.rateComment(id, rating)
    } catch {
      setComments((prev) => prev.map((c) => (c.id === id ? { ...c, rating: undefined, status: 'open' } : c)))
    }
  }

  // Only comments left on THIS version have span offsets guaranteed valid
  // against the currently displayed text — older comments still show in
  // the list below (with their own before/after), just not marked inline.
  const openComments = comments
    .filter((c) => c.status === 'open' && c.version_id === versionId)
    .sort((a, b) => a.span_start - b.span_start)

  // The full open-comment list shown below the text, regardless of which
  // version they were left on — resolved comments (thumbs-up) disappear.
  const openCardComments = comments.filter((c) => c.status === 'open').sort((a, b) => a.created_at.localeCompare(b.created_at))
  const segments: { text: string; commentId: number | null }[] = []
  let cursor = 0
  for (const c of openComments) {
    if (c.span_start < cursor) continue // overlapping spans: skip for v1
    if (c.span_start > cursor) segments.push({ text: text.slice(cursor, c.span_start), commentId: null })
    segments.push({ text: text.slice(c.span_start, c.span_end), commentId: c.id })
    cursor = c.span_end
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), commentId: null })

  return (
    <div>
      <div
        ref={containerRef}
        onMouseUp={handleMouseUp}
        className={cn(
          'relative text-foreground whitespace-pre-wrap',
          compact ? 'text-xs' : heading ? 'text-lg font-semibold' : 'text-base'
        )}
      >
        {segments.map((seg, i) =>
          seg.commentId ? (
            <mark key={i} className="bg-amber-200/70 dark:bg-amber-500/30 rounded-sm">
              {seg.text}
            </mark>
          ) : (
            <span key={i}>{seg.text}</span>
          )
        )}

        {pending && (
          <div
            ref={popoverRef}
            className={cn(
              'absolute z-10 -translate-x-1/2 bg-surface-card border border-surface-border rounded-lg shadow-xl p-3 w-64',
              pending.placement === 'above' && '-translate-y-full'
            )}
            style={{
              left: pending.x,
              top: pending.placement === 'above' ? pending.y - 8 : pending.y + 8,
            }}
          >
            <p className="text-xs text-subtle mb-1.5 truncate">&ldquo;{pending.quoted}&rdquo;</p>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as Category)}
              className="w-full mb-1.5 bg-surface border border-surface-border rounded px-2 py-1 text-xs text-foreground"
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <textarea
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              placeholder="What's off about this?"
              rows={2}
              autoFocus
              disabled={saving}
              className="w-full bg-surface border border-surface-border rounded px-2 py-1 text-xs text-foreground resize-none focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-60"
            />
            <div className="flex justify-end gap-1.5 mt-1.5">
              <Button variant="ghost" size="xs" disabled={saving} onClick={() => setPending(null)}>
                Cancel
              </Button>
              <Button size="xs" loading={saving} disabled={!commentText.trim()} onClick={handleSaveComment}>
                {saving && autoRevise ? 'Retranslating…' : 'Comment'}
              </Button>
            </div>
          </div>
        )}
      </div>

      {saving && autoRevise && (
        <div className="mt-2 flex items-center gap-2 text-xs text-muted">
          <Spinner size="sm" />
          Revising the translation based on your comment…
        </div>
      )}

      {openCardComments.length > 0 && (
        <div
          className={cn(
            'border-t border-surface-border',
            compact ? 'mt-1.5 pt-1.5 space-y-1.5' : 'mt-4 pt-3 space-y-2.5'
          )}
        >
          {!compact && (
            <p className="text-xs font-medium text-muted">
              {openCardComments.length} comment{openCardComments.length !== 1 ? 's' : ''}
            </p>
          )}
          {openCardComments.map((c) => {
            // A revision was attempted but produced no actual change —
            // the model's response was unusable even after a retry, so
            // the fallback kept the prior text. Distinct from "not
            // attempted yet" (document mode, pre-regenerate): here there
            // IS a result, it's just not a fix, so it needs its own
            // honest message rather than a confusing empty diff.
            const attempted = c.corrected_output != null
            const hasRealFix = attempted && c.corrected_output !== c.mt_output
            return (
              <div
                key={c.id}
                className={cn(
                  'rounded-lg border border-surface-border',
                  compact ? 'px-2 py-1 text-xs' : 'px-3 py-2 text-sm'
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs px-1.5 py-0.5 rounded bg-surface text-subtle uppercase tracking-wide">
                    {c.category}
                  </span>
                  {!readOnly && (
                    <div className="flex items-center gap-2">
                      {!hasRealFix && (
                        <button
                          type="button"
                          onClick={() => handleResolve(c.id)}
                          className="text-xs text-subtle hover:text-brand-600"
                        >
                          Resolve
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleDelete(c.id)}
                        className="text-subtle hover:text-red-500"
                        aria-label="Delete"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
                <p className="text-xs text-subtle mt-1 truncate">&ldquo;{c.quoted_text}&rdquo;</p>
                <p className="text-foreground mt-0.5">{c.comment_text}</p>
                <p className="text-xs text-subtle mt-1">{c.created_by_name}</p>

                {hasRealFix && (
                  <div className="mt-2 pt-2 border-t border-surface-border/60">
                    <p className="text-xs font-medium text-muted mb-1">↳ Revised to</p>
                    <p className={cn('whitespace-pre-wrap text-foreground', compact ? 'text-xs' : 'text-sm')}>
                      {c.corrected_output}
                    </p>
                    {c.reasoning && (
                      <p className={cn('mt-1 text-subtle italic', compact ? 'text-[11px]' : 'text-xs')}>
                        {c.reasoning}
                      </p>
                    )}
                    {!readOnly && (
                      <div className="mt-2">
                        <RatingButtons rating={c.rating ?? null} onRate={(r) => handleRate(c.id, r)} compact={compact} />
                      </div>
                    )}
                  </div>
                )}

                {attempted && !hasRealFix && (
                  <div className="mt-2 pt-2 border-t border-surface-border/60">
                    <p className={cn('text-amber-600 dark:text-amber-400', compact ? 'text-xs' : 'text-sm')}>
                      {c.reasoning ||
                        'Could not produce a usable fix for this. Try a clearer or more specific comment, or ' +
                          'resolve and select the text again.'}
                    </p>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
