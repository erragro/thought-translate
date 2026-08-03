import { useRef, useState } from 'react'
import { FileText, Upload, X, Languages, ArrowLeftRight, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, Download } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Spinner } from '@/components/ui/Spinner'
import { cn } from '@/lib/cn'
import {
  translateApi,
  type TranslateResponse,
  type DocumentTranslateResponse,
  type NewVersion,
  type Comment,
  type TranslateMode,
  type OutputScript,
  type NumeralsFormat,
} from '@/api/translate.api'
import { CommentableText } from '@/components/translate/CommentableText'
import { CopyButton } from '@/components/translate/CopyButton'
import { Tooltip } from '@/components/ui/Tooltip'

interface VersionEntry {
  version_id: number
  version_number: number
  translation: string
}

type Mode = 'paste' | 'upload'

interface Lang {
  code: string
  label: string
}

// First supported pair only, per 2026-08-02 decision (CONCEPT.md §0 /
// "Decided: first language pair is Hindi ↔ English"). Both directions
// route through Sarvam only, per the 2026-08-03 build decision — no
// Gemini involved right now.
const SUPPORTED_LANGUAGES: Lang[] = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'Hindi' },
]

function languageLabel(code: string): string {
  return SUPPORTED_LANGUAGES.find((l) => l.code === code)?.label ?? code
}

// Sarvam's mayura:v1 options (confirmed 2026-08-03 via Sarvam's own
// docs) — tone/register, roman-vs-native script, and native-vs-plain
// numerals. Applies to both paste and document upload.
const TONE_OPTIONS: { value: TranslateMode; label: string; hint: string }[] = [
  { value: 'formal', label: 'Formal', hint: 'Polite, standard tone. Good for official or business text.' },
  { value: 'modern-colloquial', label: 'Modern colloquial', hint: 'Casual, everyday spoken style.' },
  { value: 'classic-colloquial', label: 'Classic colloquial', hint: 'Traditional everyday spoken style.' },
  { value: 'code-mixed', label: 'Code-mixed', hint: 'Mixes in common English words, like everyday bilingual speech.' },
]

const SCRIPT_OPTIONS: { value: OutputScript; label: string; hint: string }[] = [
  { value: 'fully-native', label: 'Native script', hint: 'Written in the target language\'s own script, for example Devanagari for Hindi.' },
  { value: 'roman', label: 'Roman script', hint: 'Written using English letters (transliterated), for example "Namaste" instead of "नमस्ते".' },
  { value: 'spoken-form-in-native', label: 'Native (spoken style)', hint: 'Native script, but written the way it is actually spoken.' },
]

const NUMERALS_OPTIONS: { value: NumeralsFormat; label: string; hint: string }[] = [
  { value: 'international', label: 'Roman numerals (0-9)', hint: 'Numbers written as 0-9, for example 25.' },
  { value: 'native', label: 'Native numerals', hint: 'Numbers written in the target language\'s own digits, for example २५ for 25 in Hindi.' },
]

const ACCEPTED_EXTENSIONS = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.jpg', '.jpeg', '.png']
const ACCEPTED_ATTR = ACCEPTED_EXTENSIONS.join(',')

function isAcceptedFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))
}

// Table comments are keyed by a single chunk_index int, same column the
// prose path uses for paragraph index — a cell's (row, col) is encoded as
// row * CELL_CHUNK_COL_MULTIPLIER + col. Must match
// document_pipeline.py's CELL_CHUNK_COL_MULTIPLIER exactly.
const CELL_CHUNK_COL_MULTIPLIER = 100_000
function encodeCellChunkIndex(row: number, col: number): number {
  return row * CELL_CHUNK_COL_MULTIPLIER + col
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// A client-side timeout (ECONNABORTED, or no `.response` at all) means the
// browser gave up waiting — it does NOT mean the backend failed. Distinguish
// that from a real backend error (which populates `.response.data.detail`)
// so the message doesn't send someone chasing a non-existent backend/API-key
// problem when the real issue is just "large document, still processing."
function describeRequestError(err: unknown, fallback: string): string {
  const axiosError = err as { code?: string; message?: string; response?: { data?: { detail?: string } } }
  if (axiosError.response) {
    return axiosError.response.data?.detail ?? fallback
  }
  if (axiosError.code === 'ECONNABORTED' || /timeout/i.test(axiosError.message ?? '')) {
    return "This is taking longer than expected. Large documents can take a few minutes, it may still complete in the background, so check back shortly or try a smaller document."
  }
  return fallback
}

export default function TranslatePage() {
  const [sourceLang, setSourceLang] = useState('en')
  const [targetLang, setTargetLang] = useState('hi')
  const [mode, setMode] = useState<Mode>('paste')
  const [tone, setTone] = useState<TranslateMode>('formal')
  const [outputScript, setOutputScript] = useState<OutputScript>('fully-native')
  const [numeralsFormat, setNumeralsFormat] = useState<NumeralsFormat>('international')
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [fileError, setFileError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<TranslateResponse | null>(null)
  const [docResult, setDocResult] = useState<DocumentTranslateResponse | null>(null)
  const [showNotes, setShowNotes] = useState(false) // off by default — CONCEPT.md decision
  const [versions, setVersions] = useState<VersionEntry[]>([])
  const [versionIndex, setVersionIndex] = useState(0)
  const [pendingDocComments, setPendingDocComments] = useState(0)
  const [regenerating, setRegenerating] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const activeVersion = versions[versionIndex]
  const isLatestVersion = versionIndex === versions.length - 1

  const handleRevised = (newVersion: NewVersion) => {
    setVersions((prev) => {
      const next = [...prev, newVersion]
      setVersionIndex(next.length - 1) // jump to the new latest, per spec
      return next
    })
  }

  const handleDocCommentAdded = (_comment: Comment) => {
    setPendingDocComments((n) => n + 1)
  }

  const handleRegenerate = async () => {
    if (!docResult) return
    setRegenerating(true)
    try {
      const res = await translateApi.regenerate(docResult.version_id)
      setDocResult({
        thread_id: res.data.thread_id,
        version_id: res.data.version_id,
        kind: res.data.kind,
        paragraphs: res.data.paragraphs,
        is_heading: res.data.is_heading,
        rows: res.data.rows,
        cache_hits: 0,
        cache_misses: 0,
      })
      setPendingDocComments(0)
    } catch (err: unknown) {
      setNotice(describeRequestError(err, 'Regeneration failed. Check the backend is running.'))
    } finally {
      setRegenerating(false)
    }
  }

  const handleDownload = async () => {
    if (!docResult) return
    setDownloading(true)
    try {
      const res = await translateApi.downloadDocument(docResult.thread_id)
      const disposition = (res.headers as Record<string, string>)['content-disposition']
      const match = disposition?.match(/filename\*=UTF-8''([^;]+)/)
      const filename = match ? decodeURIComponent(match[1]) : 'translated-document'
      const url = window.URL.createObjectURL(new Blob([res.data]))
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    } catch {
      setNotice('Download failed. The backend may be unreachable.')
    } finally {
      setDownloading(false)
    }
  }

  const handleSwapLanguages = () => {
    setSourceLang(targetLang)
    setTargetLang(sourceLang)
  }

  const handleFileSelected = (selected: File | null) => {
    setFileError('')
    if (!selected) {
      setFile(null)
      return
    }
    if (!isAcceptedFile(selected)) {
      setFileError(`Unsupported file type. Accepted: ${ACCEPTED_EXTENSIONS.join(', ')}`)
      return
    }
    setFile(selected)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const dropped = e.dataTransfer.files?.[0] ?? null
    handleFileSelected(dropped)
  }

  const hasInput = mode === 'paste' ? text.trim().length > 0 : file !== null
  const canSubmit = hasInput && sourceLang !== targetLang && !loading

  const handleSubmit = async () => {
    setNotice('')
    setResult(null)
    setDocResult(null)
    setVersions([])
    setVersionIndex(0)
    setPendingDocComments(0)
    setLoading(true)

    const options = { mode: tone, output_script: outputScript, numerals_format: numeralsFormat }

    try {
      if (mode === 'paste') {
        const res = await translateApi.run(sourceLang, targetLang, text, options, 'paste')
        setResult(res.data)
        setVersions([{ version_id: res.data.version_id, version_number: 1, translation: res.data.translation }])
        setVersionIndex(0)
        setShowNotes(false)
      } else if (file) {
        const res = await translateApi.runDocument(sourceLang, targetLang, file, options)
        setDocResult(res.data)
      }
    } catch (err: unknown) {
      setNotice(
        describeRequestError(err, 'Translation failed. Check the backend is running and SARVAM_API_KEY is set.')
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <div className="flex items-center gap-2.5 mb-1">
        <Languages className="w-5 h-5 text-brand-600" />
        <h1 className="text-xl font-semibold text-foreground">Thought Translate</h1>
      </div>
      <p className="text-muted text-sm mb-8">
        Translation that mirrors thought, not words.
      </p>

      {/* Language pair selector */}
      <div className="flex items-center gap-2 mb-6">
        <Tooltip content={`Your text is in ${languageLabel(sourceLang)}`}>
          <select
            value={sourceLang}
            onChange={(e) => setSourceLang(e.target.value)}
            aria-label="Source language"
            className="bg-surface-card border border-surface-border rounded-md px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-brand-500 focus:border-brand-500"
          >
            {SUPPORTED_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </Tooltip>

        <button
          type="button"
          onClick={handleSwapLanguages}
          aria-label="Swap languages"
          className="p-2 rounded-md text-muted hover:text-foreground hover:bg-surface-card border border-transparent hover:border-surface-border transition-colors"
        >
          <ArrowLeftRight className="w-4 h-4" />
        </button>

        <Tooltip content={`We will translate it into ${languageLabel(targetLang)}`}>
          <select
            value={targetLang}
            onChange={(e) => setTargetLang(e.target.value)}
            aria-label="Target language"
            className="bg-surface-card border border-surface-border rounded-md px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-brand-500 focus:border-brand-500"
          >
            {SUPPORTED_LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </Tooltip>

        {sourceLang === targetLang && (
          <span className="text-xs text-red-400 ml-1">Source and target must be different.</span>
        )}
      </div>

      {/* Tone, script, and numerals — applies to both paste and upload */}
      <div className="flex items-center gap-2 mb-6">
        <Tooltip content={TONE_OPTIONS.find((o) => o.value === tone)?.hint ?? ''}>
          <select
            value={tone}
            onChange={(e) => setTone(e.target.value as TranslateMode)}
            aria-label="Tone"
            className="bg-surface-card border border-surface-border rounded-md px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-brand-500 focus:border-brand-500"
          >
            {TONE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Tooltip>

        <Tooltip content={SCRIPT_OPTIONS.find((o) => o.value === outputScript)?.hint ?? ''}>
          <select
            value={outputScript}
            onChange={(e) => setOutputScript(e.target.value as OutputScript)}
            aria-label="Script"
            className="bg-surface-card border border-surface-border rounded-md px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-brand-500 focus:border-brand-500"
          >
            {SCRIPT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Tooltip>

        <Tooltip content={NUMERALS_OPTIONS.find((o) => o.value === numeralsFormat)?.hint ?? ''}>
          <select
            value={numeralsFormat}
            onChange={(e) => setNumeralsFormat(e.target.value as NumeralsFormat)}
            aria-label="Numerals"
            className="bg-surface-card border border-surface-border rounded-md px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-brand-500 focus:border-brand-500"
          >
            {NUMERALS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Tooltip>
      </div>

      {/* Mode toggle */}
      <div className="inline-flex bg-surface-card border border-surface-border rounded-lg p-1 mb-4">
        <button
          type="button"
          onClick={() => setMode('paste')}
          className={cn(
            'px-4 py-1.5 text-sm font-medium rounded-md transition-colors',
            mode === 'paste' ? 'bg-brand-600 text-white' : 'text-muted hover:text-foreground'
          )}
        >
          Paste text
        </button>
        <button
          type="button"
          onClick={() => setMode('upload')}
          className={cn(
            'px-4 py-1.5 text-sm font-medium rounded-md transition-colors',
            mode === 'upload' ? 'bg-brand-600 text-white' : 'text-muted hover:text-foreground'
          )}
        >
          Upload document
        </button>
      </div>

      {/* Input surface — mode-dependent */}
      {mode === 'paste' ? (
        <div className="bg-surface-card border border-surface-border rounded-xl p-4">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste or type the text you want translated…"
            rows={10}
            className={cn(
              'w-full bg-surface border border-surface-border rounded-md px-3 py-2.5 text-sm text-foreground resize-y',
              'placeholder:text-subtle',
              'focus:outline-none focus:ring-1 focus:ring-brand-500 focus:border-brand-500'
            )}
          />
          <div className="flex justify-end mt-1.5">
            <span className="text-xs text-subtle">{text.length.toLocaleString()} characters</span>
          </div>
        </div>
      ) : (
        <div className="bg-surface-card border border-surface-border rounded-xl p-4">
          {!file ? (
            <div
              onDragOver={(e) => {
                e.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed py-12 cursor-pointer transition-colors',
                dragOver ? 'border-brand-500 bg-brand-50 dark:bg-brand-600/10' : 'border-surface-border hover:border-surface-muted'
              )}
            >
              <Upload className="w-8 h-8 text-subtle" />
              <p className="text-sm text-foreground font-medium">Drop a file here, or click to browse</p>
              <p className="text-xs text-subtle">PDF, Word, Excel, CSV, or an image (JPG/PNG, transcribed via OCR)</p>
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_ATTR}
                className="hidden"
                onChange={(e) => handleFileSelected(e.target.files?.[0] ?? null)}
              />
            </div>
          ) : (
            <div className="flex items-center justify-between rounded-lg border border-surface-border px-4 py-3">
              <div className="flex items-center gap-3 min-w-0">
                <FileText className="w-5 h-5 text-brand-600 shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm text-foreground font-medium truncate">{file.name}</p>
                  <p className="text-xs text-subtle">{formatBytes(file.size)}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => handleFileSelected(null)}
                className="text-subtle hover:text-foreground shrink-0"
                aria-label="Remove file"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}
          {fileError && <p className="text-xs text-red-400 mt-2">{fileError}</p>}
        </div>
      )}

      <div className="flex items-center justify-between mt-4">
        <p className="text-xs text-subtle">
          Excel/CSV translate cell contents in place, structure and formulas stay untouched.
        </p>
        <Button disabled={!canSubmit} loading={loading} onClick={handleSubmit}>
          Translate
        </Button>
      </div>

      {loading && (
        <div className="mt-4 flex items-center gap-2.5 text-sm text-muted">
          <Spinner size="sm" />
          {mode === 'paste' ? 'Running Understand → Research → Synthesize → Validate…' : 'Extracting and translating the document…'}
        </div>
      )}

      {notice && (
        <div className="mt-4 rounded-lg border border-surface-border bg-surface-card px-4 py-3 text-sm text-muted">
          {notice}
        </div>
      )}

      {result && activeVersion && (
        <div className="mt-4 bg-surface-card border border-surface-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-2 gap-3">
            <p className="text-xs font-medium text-muted">
              {isLatestVersion ? 'Translation: select any text to leave a comment' : 'Translation (earlier version, read-only)'}
            </p>

            <div className="flex items-center gap-2 shrink-0">
              {versions.length > 1 && (
                <div className="flex items-center gap-1 text-xs text-subtle">
                  <button
                    type="button"
                    onClick={() => setVersionIndex((i) => Math.max(0, i - 1))}
                    disabled={versionIndex === 0}
                    aria-label="Previous version"
                    className="p-0.5 rounded hover:bg-surface hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" />
                  </button>
                  <span className="tabular-nums">
                    v{activeVersion.version_number} of {versions.length}
                  </span>
                  <button
                    type="button"
                    onClick={() => setVersionIndex((i) => Math.min(versions.length - 1, i + 1))}
                    disabled={isLatestVersion}
                    aria-label="Next version"
                    className="p-0.5 rounded hover:bg-surface hover:text-foreground disabled:opacity-30 disabled:hover:bg-transparent"
                  >
                    <ChevronRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
              <CopyButton text={activeVersion.translation} />
            </div>
          </div>

          <CommentableText
            key={activeVersion.version_id}
            threadId={result.thread_id}
            versionId={activeVersion.version_id}
            text={activeVersion.translation}
            readOnly={!isLatestVersion}
            onRevised={handleRevised}
          />

          {result.notes && (
            <div className="mt-3 pt-3 border-t border-surface-border">
              <button
                type="button"
                onClick={() => setShowNotes((v) => !v)}
                className="flex items-center gap-1 text-xs text-muted hover:text-foreground"
              >
                {showNotes ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                Translator's notes
              </button>
              {showNotes && <p className="mt-1.5 text-sm text-muted">{result.notes}</p>}
            </div>
          )}

          <div className="mt-3 pt-3 border-t border-surface-border flex items-center justify-between text-xs text-subtle">
            <span>
              {versionIndex === 0
                ? result.from_cache
                  ? 'served from cache'
                  : `${result.token_usage.total_tokens.toLocaleString()} tokens (${result.token_usage.prompt_tokens.toLocaleString()} in / ${result.token_usage.completion_tokens.toLocaleString()} out)`
                : 'revised from a comment'}
            </span>
            <span>thread #{result.thread_id}</span>
          </div>
        </div>
      )}

      {docResult && (
        <div className="mt-4 bg-surface-card border border-surface-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-2 gap-3">
            <p className="text-xs font-medium text-muted">
              {docResult.kind === 'prose'
                ? 'Translated document: select any paragraph\'s text to leave a comment'
                : 'Translated document: select any cell\'s text to leave a comment'}
            </p>
            <div className="flex items-center gap-2 shrink-0">
              {pendingDocComments > 0 && (
                <Button size="xs" loading={regenerating} onClick={handleRegenerate}>
                  {regenerating
                    ? 'Regenerating…'
                    : `Regenerate with ${pendingDocComments} comment${pendingDocComments !== 1 ? 's' : ''}`}
                </Button>
              )}
              <CopyButton
                text={
                  docResult.kind === 'prose'
                    ? (docResult.paragraphs ?? []).join('\n\n')
                    : (docResult.rows ?? []).map((row) => row.join('\t')).join('\n')
                }
              />
              <Button variant="outline" size="xs" loading={downloading} onClick={handleDownload}>
                <Download className="w-3 h-3" />
                Download
              </Button>
            </div>
          </div>

          {docResult.kind === 'prose' ? (
            <div className="space-y-4">
              {(docResult.paragraphs ?? []).map((p, i) => (
                <div key={`${docResult.version_id}-${i}`}>
                  <CommentableText
                    threadId={docResult.thread_id}
                    versionId={docResult.version_id}
                    text={p}
                    chunkIndex={i}
                    autoRevise={false}
                    onCommentAdded={handleDocCommentAdded}
                    heading={docResult.is_heading?.[i] ?? false}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <tbody>
                  {(docResult.rows ?? []).map((row, ri) => (
                    <tr key={ri} className={ri === 0 ? 'font-medium' : ''}>
                      {row.map((cell, ci) => {
                        const cellChunkIndex = encodeCellChunkIndex(ri, ci)
                        return (
                          <td key={ci} className="border border-surface-border px-2 py-1.5 text-foreground align-top">
                            <CommentableText
                              threadId={docResult.thread_id}
                              versionId={docResult.version_id}
                              text={cell}
                              chunkIndex={cellChunkIndex}
                              autoRevise={false}
                              onCommentAdded={handleDocCommentAdded}
                              compact
                            />
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {regenerating && (
            <div className="mt-3 flex items-center gap-2 text-xs text-muted">
              <Spinner size="sm" />
              Revising the flagged paragraphs, this can take a little while…
            </div>
          )}

          <div className="mt-3 pt-3 border-t border-surface-border flex items-center justify-between text-xs text-subtle">
            <span>
              {docResult.cache_hits > 0 || docResult.cache_misses > 0
                ? `${docResult.cache_hits} cached · ${docResult.cache_misses} translated`
                : 'revised from comments'}
            </span>
            <span>thread #{docResult.thread_id}</span>
          </div>
        </div>
      )}
    </div>
  )
}
