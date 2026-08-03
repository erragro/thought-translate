import { apiClient } from './client'

export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface StageTrace {
  stage: string
  output: Record<string, unknown>
  prompt_tokens: number
  completion_tokens: number
  latency_ms: number
}

export interface TranslateResponse {
  thread_id: number
  version_id: number
  translation: string
  notes: string
  from_cache: boolean
  token_usage: TokenUsage
  stages: StageTrace[]
}

export interface DocumentTranslateResponse {
  thread_id: number
  version_id: number
  kind: 'prose' | 'table'
  paragraphs: string[] | null
  is_heading: boolean[] | null
  rows: string[][] | null
  cache_hits: number
  cache_misses: number
}

export interface Comment {
  id: number
  version_id: number
  span_start: number
  span_end: number
  quoted_text: string
  comment_text: string
  category: 'accuracy' | 'fluency' | 'terminology' | 'style'
  status: 'open' | 'resolved'
  created_by: number
  created_by_name: string
  created_at: string
  resolved_at: string | null
  chunk_index: number | null
  /** Present once this comment has produced an actual revision — the
   * before/after text for the one chunk it applies to, plus the
   * reviewer's rating on that specific fix (§5/§6 correction corpus). */
  mt_output?: string | null
  corrected_output?: string | null
  rating?: Rating | null
}

export interface RegenerateResponse {
  thread_id: number
  version_id: number
  version_number: number
  kind: 'prose' | 'table'
  paragraphs: string[] | null
  is_heading: boolean[] | null
  rows: string[][] | null
  /** Which paragraph/cell chunk_indices this regenerate call actually
   * touched — document mode uses this to know which chunks to show a
   * rating control on. */
  revised_chunk_indices: number[]
}

export type Rating = 'up' | 'down'

// Sarvam's mayura:v1 model options (confirmed 2026-08-03 via Sarvam's own
// docs): mode is tone/register, output_script is roman vs native script,
// numerals_format is native digits vs plain 0-9.
export type TranslateMode = 'formal' | 'modern-colloquial' | 'classic-colloquial' | 'code-mixed'
export type OutputScript = 'roman' | 'fully-native' | 'spoken-form-in-native'
export type NumeralsFormat = 'international' | 'native'

export interface TranslateOptions {
  mode: TranslateMode
  output_script: OutputScript
  numerals_format: NumeralsFormat
}

export interface NewVersion {
  version_id: number
  version_number: number
  translation: string
}

export interface AddCommentResponse {
  comment: Comment
  new_version: NewVersion | null
}

export interface VersionSummary {
  id: number
  version_number: number
  translated_text: string
  translator_notes: string
  created_at: string
}

export const translateApi = {
  run: (
    source_lang: string,
    target_lang: string,
    text: string,
    options: TranslateOptions,
    input_mode: 'paste' | 'upload' = 'paste'
  ) =>
    apiClient.post<TranslateResponse>('/translate/run', {
      source_lang,
      target_lang,
      text,
      input_mode,
      mode: options.mode,
      output_script: options.output_script,
      numerals_format: options.numerals_format,
    }),

  runDocument: (source_lang: string, target_lang: string, file: File, options: TranslateOptions) => {
    const form = new FormData()
    form.append('source_lang', source_lang)
    form.append('target_lang', target_lang)
    form.append('file', file)
    form.append('mode', options.mode)
    form.append('output_script', options.output_script)
    form.append('numerals_format', options.numerals_format)
    return apiClient.post<DocumentTranslateResponse>('/translate/run-document', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // Bulk upload is N sequential Sarvam calls, one per paragraph/cell
      // (up to MAX_PARAGRAPHS=100 / MAX_TEXT_CELLS=200 — see
      // document_pipeline.py) — the shared client's 30s default is tuned
      // for single-call endpoints and isn't enough for a large document,
      // even though the backend itself finishes fine.
      timeout: 300_000,
    })
  },

  listComments: (versionId: number) => apiClient.get<Comment[]>(`/translate/versions/${versionId}/comments`),

  /** Every comment across the whole thread (every version), each carrying
   * its own resulting correction if one exists — comments persist across
   * a revision instead of disappearing when the displayed version moves
   * forward, so multi-turn refinement on the same spot stays visible. */
  listThreadComments: (threadId: number) => apiClient.get<Comment[]>(`/translate/threads/${threadId}/comments`),

  addComment: (
    versionId: number,
    payload: {
      span_start: number
      span_end: number
      quoted_text: string
      comment_text: string
      category: string
      chunk_index?: number
      auto_revise?: boolean
    }
  ) => apiClient.post<AddCommentResponse>(`/translate/versions/${versionId}/comments`, payload),

  resolveComment: (commentId: number) => apiClient.patch<Comment>(`/translate/comments/${commentId}/resolve`),

  deleteComment: (commentId: number) => apiClient.delete(`/translate/comments/${commentId}`),

  listVersions: (threadId: number) => apiClient.get<VersionSummary[]>(`/translate/threads/${threadId}/versions`),

  regenerate: (versionId: number) =>
    // Same reasoning as runDocument's timeout override — one revision
    // call per commented paragraph, sequential.
    apiClient.post<RegenerateResponse>(`/translate/versions/${versionId}/regenerate`, undefined, {
      timeout: 300_000,
    }),

  downloadDocument: (threadId: number) =>
    apiClient.get(`/translate/threads/${threadId}/download`, { responseType: 'blob' }),

  /** Thumbs up/down on the fix a specific comment produced — up also
   * resolves the comment server-side (it disappears from the open list);
   * down leaves it open for a follow-up comment on the same spot. */
  rateComment: (commentId: number, rating: Rating) =>
    apiClient.patch<{ updated: number; comment: Comment | null }>(`/translate/comments/${commentId}/rating`, {
      rating,
    }),
}
