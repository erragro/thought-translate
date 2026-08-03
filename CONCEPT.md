# Thought Translate — Concept Note
### (working name — rename freely)

## Platform framing (added 2026-08-02)

The login system built first (JWT + RBAC, `backend/app/auth/`) is not
specific to translation — it's the shell for a multi-module workspace.
Thought Translate is **module #1**, surfaced as a sidebar entry, gated by
the `translate` permission that already exists in `ALL_MODULES`
(`backend/app/auth/service.py`). Future modules register the same way:
add to `ALL_MODULES`, add a sidebar entry, add a route. This is the same
pattern kirana_kart_final's admin console uses (dashboard/tickets/
taxonomy/... as sibling modules under one login), which is exactly why
the permission model was built as a per-module `{view, edit, admin}` map
instead of a single translation-specific flag.

Practical effect: the frontend needs an `AppShell` + `Sidebar` (module
nav, extensible) wrapping module pages, instead of `HomePage` being the
whole app. The `thought-translate` directory name is now a bit narrow for
"the platform," but renaming mid-build has real cost (running processes,
`.claude/launch.json` entry, memory files) — flagged, not yet decided.

## The problem with literal translation

Most translation, including naive LLM prompting ("translate this to Hindi"),
still works by pattern-matching the source text and producing the
statistically likely equivalent — it doesn't actually stop to figure out
*what the sentence means* before deciding how to say it in another language.
That's why translation tools reliably fail at:

- **Idioms** — "it's raining cats and dogs" translated word-for-word is
  nonsense in almost every other language. The correct translation requires
  first recognizing it's an idiom, then finding the *target* language's
  equivalent idiom for "raining heavily," not a literal rendering.
- **Proper nouns and current references** — a name, brand, meme, or event
  reference might not transliterate obviously. (Note: the Research stage
  below resolved to a curated grammar/guidelines reference rather than
  live web search — real-time lookup of very recent proper nouns is
  correspondingly out of scope for now, not a v1 guarantee.)
- **Cultural context** — a phrase's correct translation can depend on facts
  not present in the sentence itself (a festival, a regional custom, a
  political reference) — facts a model may know imperfectly or not at all.
- **Tone and register** — sarcasm, formality level, humor — these often
  don't survive literal translation even when every individual word is
  rendered "correctly."

## The idea

A translation system that treats translation as a **three-stage reasoning
process**, not a single pattern-match:

1. **Understand the thought** — before translating anything, the model
   extracts what the source text actually *means*: intent, tone, any
   idioms/references/ambiguous terms it isn't fully confident about.
2. **Research what it doesn't know — only when genuinely uncertain, not
   every time (decided 2026-08-03).** For anything Stage 1 actually
   flagged as uncertain, consult a curated reference: grammar rules and
   translation guidelines for the target language, not open internet
   search (decided 2026-08-03 — see "Research: reference lookup, not web
   search" below). Deeper thinking only kicks in when something is
   genuinely ambiguous; the common case should be fast.
3. **Synthesize the translation** — combining the extracted meaning with
   the relevant grammar/style guidance, it produces a translation that
   mirrors the original *thought*, not the original *words* — and can
   optionally show its reasoning ("translator's notes": *"rendered as
   [target-language idiom] rather than literally, since the source
   phrase is idiomatic for 'very fast'"*), surfaced via an **optional
   toggle, not shown by default** (decided 2026-08-03).

That last part — showing the reasoning, not just the output — is itself a
differentiator. Most translation tools are a black box; this one can show
*why* it made a translation choice, which is valuable both for trust and for
anyone actually trying to learn the nuance between languages.

## Scope correction: this is a full-stack product, not a pipeline

The first draft of this doc scoped a backend reasoning pipeline. The actual
ask is bigger and worth naming explicitly: a product with real input
handling, a persistent UI, and a **stateful revision loop** — the
translation isn't a one-shot output, it's the start of a conversation the
user can push back on.

## Rough architecture (revised)

```
INPUT LAYER
  Text entry  ── OR ──  File upload (image / PDF / doc)
                              │
                              ▼
                    Text extraction — for image-based or scanned
                    content, feed it straight into Gemini's native
                    multimodal input rather than a separate OCR step;
                    for native-text PDFs/docs, a plain text-extraction
                    library is enough. No bespoke OCR pipeline needed.
        │
        ▼
┌─────────────────────────────────────────────┐
│ Stage 1 — Understand                          │
│ Gemini extracts: core meaning, tone/register, │
│ flagged uncertain elements (idioms, proper    │
│ nouns, references it isn't confident about)   │
└─────────────────────────────────────────────┘
        │  list of things to verify (may be empty)
        ▼
┌─────────────────────────────────────────────┐
│ Stage 2 — Research (only if Stage 1 flagged   │
│ something genuinely uncertain — not every     │
│ request)                                      │
│ Looks up the flagged item against a curated   │
│ grammar-rules + translation-guidelines        │
│ reference for the target language. NOT open   │
│ internet search (decided 2026-08-03).         │
└─────────────────────────────────────────────┘
        │  meaning + research findings
        ▼
┌─────────────────────────────────────────────┐
│ Stage 3 — Synthesize                          │
│ Gemini produces the translation reflecting    │
│ meaning (not literal words), plus optional    │
│ translator's notes explaining non-obvious     │
│ choices                                       │
└─────────────────────────────────────────────┘
        │
        ▼
CHAT UI — translation + notes shown as the first message in a thread
        │
        ▼
┌─────────────────────────────────────────────┐
│ Revision loop                                 │
│ User comments in the same thread ("more       │
│ formal", "that idiom feels off", "this is for │
│ a legal document, be precise not natural").   │
│ Stage 3 re-runs — now conversational, carrying│
│ the original meaning + research + full        │
│ feedback thread as context — and may trigger  │
│ Stage 2 again if the feedback surfaces a new   │
│ ambiguous term. Same pattern quickbites-bot    │
│ already uses for multi-turn state.            │
└─────────────────────────────────────────────┘
```

This means Stage 3 can no longer be one-shot — it needs to hold
conversation state (original source, extracted meaning, research findings,
every revision request and prior output) the same way quickbites-bot's
pipeline holds turn history, not regenerate from scratch each time.

## Where this connects to what's already built

- **Sarvam AI** is the provider for the first pair (Hindi↔English, "for
  now" — see the decision below); **Gemini** remains available for
  languages/directions where it's the better fit, decided per the same
  language-pair-based routing quickbites-bot uses, not a fixed split.
- **Research: reference lookup, not web search (decided 2026-08-03).**
  Open internet search was the original plan for Stage 2 (an MCP
  web-search connector) — not needed for translation. What Stage 2
  actually needs is a curated reference: grammar rules and translation
  guidelines for the target language. MCP may still be the right
  *connector shape* for this — a tool call against a curated internal
  knowledge base instead of the open web — but it's now a "build/curate
  a grammar-rules + guidelines corpus" problem, not an "integrate a web
  search API" problem. Simpler build, different resource to assemble.

## Module feature spec (added 2026-08-02)

Full requirement dump from this round, organized so each area maps to a
buildable slice.

### 0. Direction — two pipelines, not one
Translation is bidirectional and the two directions aren't symmetric
enough to treat as one pipeline with a flipped flag:
- **English → other language**
- **Other language → English**
A target-language selector belongs in the input surface (§1) alongside
the mode toggle. Provider routing follows quickbites-bot's proven split
— Gemini (via Vertex AI) handles English and Hindi in either direction;
Sarvam AI handles the other Indian languages. Which pipeline runs is
decided by the selected language pair, not guessed from the input text
(source-language detection still matters for validating the pair, the
way quickbites-bot's `language_detector` confirms what was actually
typed — but the target is an explicit user choice, not inferred).

### 1. Input — dynamic, mode-aware
Two entry modes in one input surface, not two separate pages:
- **Paste/type raw text** — a plain text box.
- **Upload a document** — PDF, Word (.docx), Excel (.xlsx), CSV.

The input box itself should adapt to the chosen mode (upload dropzone vs.
text area), not show both at once. Excel/CSV are structurally different
from PDF/Word — they're tabular, not prose. Translating a spreadsheet
means translating cell contents while preserving structure (rows,
columns, formulas untouched), not treating it as a wall of text. That's
a materially different extraction + reassembly path from PDF/Word and
should be scoped as its own case, not bolted onto the document pipeline
as an afterthought.

### 2. Output — format- and use-aware
Output rendering should match what was asked for, not always come back as
a flat block of translated text:
- A document in → a document-shaped output (preserving structure/
  formatting where feasible), not a text dump.
- A spreadsheet in → a spreadsheet-shaped output.
- Free-typed text in → free text out.
"Design-friendly" also means the *rendering* in the UI should look like
the target artifact (a document view, not raw markdown), which pushes
toward the editor surface below rather than a plain chat bubble.

### 3. Interactive editor + inline correction
Output opens in an editor view (not just a chat reply) where the user can:
- Select a specific span of text.
- Attach a comment/flag to that exact span ("this idiom feels off," "too
  formal for this context").
- Revisions apply to the flagged span in context, not a full regenerate —
  this is the concrete UI shape of the "revision loop" from the original
  concept, now spec'd as span-level annotation rather than a single
  freeform chat box. The chat-thread idea isn't gone; it's the surface
  for *general* feedback, while span-comments handle *specific* feedback.

**UX reference confirmed: Google Docs (decided 2026-08-03).** Select
text → anchored comment on that range → resolve/reply thread. This is a
well-understood, implementable interaction pattern, not something to
design from scratch — and it settles the data-model shape too: comments
are anchored to spans *within a document*, not a flat chat log. A
translation "thread" is closer to a Google Doc with a comment rail than
a messaging app.

### 4. Token usage visibility
Users can see what a translation cost in tokens (and by extension, money)
— per-request and probably a running total. This means the LLM provider
calls need token-usage accounting wired in from day one (most SDKs
return usage in the response — capture it, don't bolt it on later), and
a small usage log table.

### 5. Self-learning from corrections
The model should improve from the span-level corrections in §3 — not
just apply them once and forget. Concretely this means every correction
(original translation, the flagged span, the user's correction, and why)
gets persisted as a labeled example, not just used to patch that one
response. This is a correction *corpus*, not just a revision log.

### 6. Determinism + human-auditable trace → distillation pipeline
Every reasoning stage (Understand → Research → Synthesize) needs to log
a structured, inspectable trace — not just the final output — so a human
auditor can review a sample of real requests, confirm or correct each
stage's output, and sign off. The stated end goal: use this audited,
corrected trace data (§5 + this) as a training set to distill a smaller,
purpose-built model that replaces the general-purpose LLM in production —
same "big model bootstraps labeled data, human-audits a sample, distill
into a cheap deterministic-ish model" pattern already validated in
quickbites-bot's Cardinal engine (deterministic guardrail layer + LLM
layer, gold-standard eval methodology). This is the most architecturally
significant requirement in this round — it means the pipeline's internal
stage outputs are a first-class, persisted data model, not scratch state
thrown away after the final answer.

**The human-audit *UI* is explicitly out of scope for MVP (decided
2026-08-03).** The structured trace still gets logged from day one
(cheap, and undoing "we never captured this" later is much harder than
not building a reviewer screen yet) — what's deferred is the dedicated
reviewer-facing interface. This also resolves the open question about
whether RBAC needs a third permission tier for a reviewer role: not for
MVP.

### 7. Output quality bar — terminology, grammar, conciseness (added 2026-08-03)
Three non-negotiable properties of every translation, regardless of
direction:
- **Common, contemporary terminology, not archaic terms.** This is a
  well-known failure mode specifically in Hindi MT/localization — output
  drifts toward heavily Sanskritized, bureaucratic "shuddh Hindi"
  (e.g. *दूरभाष* for phone, *संगणक* for computer) that nobody actually
  says, instead of the words people actually use day to day (*फोन*,
  *कंप्यूटर*). The fix isn't "try harder in the prompt" — it needs a
  maintainable **term-preference list** (archaic term → preferred common
  term), checked mechanically after generation, not hoped for. This is
  the same no-code, admin-extensible-catalog pattern already used
  elsewhere in this project (guardrail rules, personas) — seed it with
  a small starter list, let it grow from real corrections (§5).
- **Grammatically correct output in the target language.** Table stakes,
  but worth stating as an explicit, checked requirement rather than an
  assumption. **How this actually gets enforced (decided 2026-08-03):**
  primarily through context quality, not a bolt-on grammar checker —
  Hindi grammar-checking tooling is immature/uncertain (flagged in the
  open questions below), so the more reliable lever is (a) Understand
  extracting enough context that Synthesize has what it needs to produce
  fluent output in the first place, and (b) grounding Synthesize with
  **golden sample sets as few-shot exemplars** for response structure —
  the same golden-standard set from `GOLDEN_STANDARD_RESEARCH.md`,
  reused for generation guidance, not just evaluation. Validate's role
  narrows to "does this look structurally consistent with the exemplars"
  rather than a hard rule-based parse.
- **Simple, not verbose.** The translation should read like something a
  person would actually say/write, not an inflated, over-formal
  rendering — this is a real risk with LLM translation in general
  (models tend toward padding and hedging), doubly so once a "Research"
  step is injecting extra context that has to be *folded into* natural
  phrasing rather than tacked on.

These three map directly onto three of MQM's eight standard dimensions
(Terminology, Fluency, Style — see `GOLDEN_STANDARD_RESEARCH.md` §3),
which is a useful confirmation: they're not ad hoc requirements, they're
exactly the categories the golden-standard evaluation is already
structured to catch.

## Adopting quickbites-bot's 4-stage pattern (added 2026-08-03)

quickbites-bot's LLM pipeline is Classify → Evaluate → Validate →
Respond, with Validate implemented as close to fully deterministic as
possible (a hard-rule guardrail engine, not another LLM call) sitting
*between* the reasoning stages and the final generation step — so a
non-compliant decision can never reach the response-drafting stage in
the first place.

The mapping isn't a straight 1:1 rename. In quickbites-bot, Validate
gates a *decision* (e.g. "is this refund amount within policy") before
any customer-facing text is drafted — decisions are checkable before
generation. Terminology/grammar/conciseness are properties of the
*generated text itself* — there's nothing to grammar-check until a
draft sentence exists. So the gate has to sit **after** a draft,
not before it, which changes the stage order relative to quickbites-bot
even though the underlying idea (a deterministic-as-possible quality
gate, separate from the generative step, able to send work back rather
than let it through) is exactly the same:

```
Understand  (~ Classify)   — meaning, tone, flagged uncertain elements;
                              triggers Research only if genuinely unsure
Research    (~ Evaluate)   — grammar-rules + translation-guidelines
                              reference lookup, only for flagged items
                              (NOT open web search — decided 2026-08-03)
Synthesize                 — produce a DRAFT translation, grounded with
                              golden-sample-set exemplars for structure
                              (decided 2026-08-03 — reuses the golden
                              standard set as few-shot guidance, not
                              just an eval benchmark)
Validate    (~ Validate)   — deterministic-as-possible gate on the draft:
                              • terminology check against the
                                archaic→common term-preference list
                                (fully deterministic — a lookup)
                              • grammar/structure check against the
                                exemplar set (not a rule-based parser —
                                Hindi grammar-check tooling is immature;
                                context quality + exemplars carry most
                                of the weight, see §7)
                              • conciseness check (a heuristic — e.g.
                                length-ratio-vs-source flag)
                              Fails → back to Synthesize with the
                              specific violations attached, not a bare
                              "try again."
Respond                    — the draft that passed Validate, returned
                              to the user (+ optional translator's notes,
                              off by default — decided 2026-08-03)
```

Same idea as quickbites-bot's Cardinal engine: keep the checkable, rule-
based part of quality control out of the generative model's hands as
much as possible, rather than trusting one big prompt to get terminology,
grammar, *and* conciseness right in a single pass.

## Decided: first language pair is Hindi ↔ English (2026-08-02)

Both directions (Hindi→English and English→Hindi) route through
**Sarvam, for now** — this reverses the earlier default of routing
Hindi through Gemini (Hindi is technically in Gemini's bucket per the §0
split, same as quickbites-bot's routing). Sarvam is an Indic-specialized
model with its own published human-expert evaluation (fluency, adequacy,
faithfulness, inclusivity — see `GOLDEN_STANDARD_RESEARCH.md` §2)
showing it outperforming much larger general models on translation
specifically, so it's a reasonable default to actually test here rather
than assume Gemini is better just because it handled Hindi fine in
quickbites-bot's *support-chat* context. "For now" is explicit — the
golden standard set (once built) is the natural way to actually compare
Gemini vs. Sarvam on Hindi↔English later with real numbers instead of
guessing.

This also resolves the golden-standard open question in
`GOLDEN_STANDARD_RESEARCH.md` — Hindi↔English is both the best-covered
pair across every benchmark surveyed there (FLORES-IN, IN22, Bhashini/
AIKosh) and the one that needs the least new infrastructure to start
scoring.

## Resolved this round (2026-08-03)

1. ~~Always research, or only when genuinely uncertain?~~ → Only when
   genuinely uncertain. Deeper thinking is the exception, not the default.
2. ~~Translator's notes: default-on or toggle?~~ → Optional toggle, not
   shown by default.
3. ~~Which MCP search server/tool?~~ → Moot — Research doesn't need open
   internet search at all. It needs a curated grammar-rules +
   translation-guidelines reference instead. Different resource to build
   (a curated corpus), not a search API integration.
4. ~~Build order across §1-7 is a lot for 30 days~~ → Accepted as
   manageable scope; no further sequencing decision needed right now.
6. ~~What does human audit look like as a UI?~~ → Out of scope for MVP.
   The structured trace still gets logged (§6); the reviewer-facing
   screen doesn't get built yet.

## Resolved via best practices (2026-08-03, round 3)

User asked to resolve the remaining three open questions by best
practice rather than further discussion. Decisions:

### A. Data model — one trace mechanism serves both §4 and §6

Don't build separate systems for "token usage" (§4) and "the audit trace"
(§6) — a pipeline stage call is the natural unit for both: every stage
call has a token cost *and* is exactly the thing an auditor needs to
inspect. One table serves both needs.

```
translation_threads
  id, user_id, source_lang, target_lang, input_mode,
  original_source_ref (raw text or uploaded-file reference),
  created_at, updated_at

translation_versions            -- one row per generated/regenerated output
  id, thread_id, version_number, translated_text,
  status (draft / validated / superseded), created_at

pipeline_stage_runs             -- serves BOTH §4 (token usage) and §6 (audit trace)
  id, version_id, stage (understand|research|synthesize|validate),
  provider, model, input_json, output_json,
  prompt_tokens, completion_tokens, latency_ms, created_at

translation_comments            -- Google-Docs-style, anchored to a version
  id, version_id, span_start, span_end, comment_text,
  category (accuracy|fluency|terminology|style|...),   -- MQM-aligned, §7/GOLDEN_STANDARD §5
  severity (minor|major), status (open|resolved),
  created_by, created_at, resolved_at

correction_examples             -- the labeled corpus for §5/§6 distillation
  id, comment_id, thread_id,
  version_before_id, version_after_id,
  approved_for_training (boolean, default false),   -- human audit gate, §6
  created_at
```

`approved_for_training` matters: not every user correction should
blindly become distillation training data without at least a lightweight
review — that's the actual link back to the "human-auditable" part of
§6, even with the reviewer *UI* deferred (a correction can be flagged
programmatically — e.g. only auto-approve high-confidence, low-severity
ones — until a real review surface exists).

### B. Validate's exemplar-match mechanism — embedding similarity first, LLM-judge as escalation only

Best practice for "does this match known-good structure" is the same
answer as picking an MT eval metric (`GOLDEN_STANDARD_RESEARCH.md` §3):
embedding-based similarity (the same Indic-COMET-style approach already
chosen for evaluation) correlates far better with human judgment than
surface heuristics, is cheap, and — critically for §6 — produces a
reproducible number, not a freeform judgment. An LLM-as-judge pass is
also legitimate and already this user's preferred testing pattern
(gold-standard/internal-benchmarking, per prior sessions), but it's
comparatively expensive and less deterministic, so it shouldn't be the
default path for every request.

Concrete mechanism: retrieve the nearest golden exemplar(s) for the
language pair (embedding similarity search), score the draft against
them, and branch on the score —
- **clearly above threshold** → pass, no LLM judge needed.
- **clearly below threshold** → fail, back to Synthesize with the score
  and nearest exemplar attached as feedback.
- **ambiguous middle band** → escalate to a single bounded LLM-judge
  sub-call for the tie-break, rather than defaulting to it every time.

This keeps the common case cheap and deterministic-shaped (in the spirit
of quickbites-bot's Cardinal engine) while still having a fallback for
genuinely ambiguous cases instead of a hard threshold that's wrong at
the margins.

### C. Grammar-rules + terminology reference — source, don't hand-author

Two different sourcing strategies for two different needs, since they
actually pull in opposite directions:

- **Grammar rules** (sentence structure, agreement, case markers): source
  from a standard pedagogical reference — e.g. NCERT's Hindi grammar
  curriculum or CIIL (Central Institute of Indian Languages) — rather
  than government Rajbhasha (Department of Official Language) materials.
  This distinction matters: Rajbhasha-style official Hindi is *precisely*
  the archaic, over-Sanskritized register §7 says to avoid, even though
  it's nominally "correct." A pedagogical-standard source gives correct
  grammar without importing that register.
- **Terminology commonality** (archaic vs. common term preference,
  §7): don't hand-curate this list from opinion — derive it empirically,
  by word/phrase frequency in a large contemporary text corpus (AI4Bharat's
  BPCC/Samanantar, already surfaced in `GOLDEN_STANDARD_RESEARCH.md`, are
  real-world-scraped and reflect actual usage). This is standard NLP
  practice for building a common-vs-rare lexicon — frequency in real
  usage, not a person's judgment call about what "sounds modern."

Both become living, versioned, admin-editable resources seeded from
these sources and grown from real corrections (§5) — same no-code-catalog
pattern as the rest of this project, not a one-time static import.

## Implementation note: the pipeline changed on contact with the real API (2026-08-03)

First build revealed two things the design above didn't (couldn't) know
in advance:

1. **`sarvam-30b` is deprecated**; `sarvam-105b` is a **reasoning
   model** that burns ~1500+ completion tokens on chain-of-thought
   before answering anything, even trivially short input. Using it for
   every Understand + Synthesize call, as originally designed, would
   have been slow and expensive for no real benefit.
2. Sarvam has a **dedicated `/translate` endpoint** (separate from chat
   completions) that already handles idioms well on its own, has no
   reasoning tax, and exposes a `mode` parameter (`formal` tested clean)
   that directly satisfies §7's terminology/grammar requirement —
   without needing a separate Understand-stage LLM call at all.

**Result: Synthesize now calls `/translate` directly; Understand and
Research are honest no-ops for v1** (logged as skipped, not faked) —
running a reasoning-model call to do what `/translate` already does
internally would reintroduce the exact cost problem avoided by finding
it. Validate's terminology check now fixes violations by deterministic
find/replace rather than an LLM retry loop, since the correction is
already known. This is a real design change from the 4-stage mapping
above, not a deviation from it in spirit — the underlying principle
(keep the LLM out of work that doesn't need it) is the same one that
motivated the Validate-as-deterministic-gate design in the first place;
it just applies one stage earlier than expected. Token-usage caching
(Redis, 30-day TTL, keyed by source/target/mode/text hash) was added
alongside this, per a follow-up request, since it directly helps with
repeated identical requests regardless of which endpoint is used.

## Status

Auth module and a working first pipeline slice are both built and
verified end-to-end with real Sarvam output (English↔Hindi). Every open
question from both review rounds was resolved before build started;
what changed after that was implementation detail (which Sarvam
endpoint, not the pipeline's shape or requirements) — see the
implementation note above. Document parsing (upload mode), the
span-annotation editor, token-usage UI beyond the raw count, the
correction corpus, and the human-audit trail are still not built.
