# Golden-Standard Translation Evaluation — Research & Proposal
### For review — nothing here is built yet

## Why this matters here specifically

The whole premise of Thought Translate is "translation that mirrors
thought, not literal words" — idioms, cultural context, tone. That claim
is only as credible as the evidence behind it. This doc surveys what
already exists (so we borrow real, vetted ground truth instead of
inventing test cases) and proposes how to combine it with the
correction-audit loop already in `CONCEPT.md` §5–6.

## 1. Existing benchmark datasets (borrow, don't rebuild)

| Dataset | What it is | Why it's useful here |
|---|---|---|
| **FLORES-200 / FLORES+ / FLORES-IN** | Meta's human-translated, multi-domain, sentence-aligned benchmark — FLORES-IN extends it to 29 Indic languages | The standard general-domain reference. Free, human-translated, no licensing friction. |
| **IN22 (AI4Bharat)** | n-way parallel benchmark across all 22 scheduled Indian languages, split into IN22-Gen (Wikipedia + Web: news, culture, legal) and **IN22-Conv** (conversational domain) | IN22-Conv is the closest existing benchmark to our actual product surface — day-to-day conversational translation, not formal prose. |
| **BPCC (Bharat Parallel Corpus Collection)** | 230M bitext pairs, the training corpus behind IndicTrans2 — largest public Indic MT dataset | Not an eval set (it's training data), but a sampling pool if the curated set needs more raw examples per language pair. |
| **Bhashini / AIKosh government datasets** | Per-language-pair benchmark sets (e.g. English↔Sindhi, Hindi↔Kannada, English↔Bengali) published via India's official open government AI data portal, under NLTM (MeitY) | Government-vetted, not just academic — useful as an independent second reference. |
| **IndicMT Eval** | A dataset built specifically to *meta-evaluate* MT metrics for Indian languages — measures which automatic metric best correlates with actual human judgment | Directly answers "which automatic score should we trust" (see §3). |

**None of these need to be built from scratch.** The realistic v1 golden
set is: sample from FLORES-IN + IN22-Gen + IN22-Conv for the language
pairs we actually ship, cross-checked against a Bhashini/AIKosh set
where one exists for that pair.

## 2. Who actually does this research (real institutions, not vague "experts")

- **AI4Bharat** (IIT Madras, led by Mitesh Khapra) — built IndicTrans2,
  BPCC, and IN22; formally serves as the Data Management Unit for
  Bhashini. The closest thing to a canonical source for Indic MT
  benchmarks right now.
- **CFILT, IIT Bombay** — founded the field in India; built the IIT
  Bombay English-Hindi Parallel Corpus and Shata-Anuvaadak (110 Indian
  language pairs); active in the WMT IndicMT shared task (including
  low-resource North-East Indian languages: Assamese, Khasi, Manipuri,
  Mizo — worth knowing about if the product ever needs to go beyond the
  10 languages Sarvam/Gemini currently cover).
- **Bhashini / NLTM** (Ministry of Electronics & IT, Government of India)
  — the official national mission; publishes datasets via AIKosh
  (India's open government AI data portal).
- **Sarvam AI itself** — already our provider for non-Hindi Indic
  languages. Their published Sarvam-Translate evaluation used
  professional language experts scoring fluency, adequacy, faithfulness
  to source structure, and inclusivity — worth adopting as a reference
  bar since we're literally calling their model.

## 3. Evaluation methodology — what to actually use

**Automatic scoring: Indic-COMET (or COMET) + chrF++, not BLEU.**
Research directly comparing metrics for Indic languages found COMET-style
neural metrics correlate with human judgment far better than n-gram
metrics like BLEU, and that a Indic-tuned variant (Indic-COMET) beats
generic COMET on most Indic languages tested. BLEU is still the most
commonly *quoted* number in MT marketing, but it's the wrong metric to
gate our own pipeline on.

**Human evaluation: MQM (Multidimensional Quality Metrics), not a bare
1–5 star rating.** MQM is the WMT shared-task gold standard: instead of
scoring a whole translation, an annotator marks *specific error spans*
in the text, tags each with a category (accuracy / fluency / terminology
/ locale convention / style) and a severity (minor / major), typically
with 2–3 annotators per segment.

**This converges directly with what's already spec'd.** The span-select,
inline-comment editor in `CONCEPT.md` §3 (users flag a specific stretch
of translated text and say what's wrong) is structurally the same
interaction as MQM error annotation. That's not a coincidence worth
losing — if the correction UI is built MQM-shaped from the start
(span + category + severity, not just a freeform comment box), every
real user correction is *simultaneously* a product feature and a
properly-labeled MQM data point, feeding the same audit/distillation
pipeline from §6 without a separate "evaluation mode" needing to be
built later.

## 4. Proposed golden-standard construction

1. **Borrowed core**: sample N sentence pairs per supported language
   pair from FLORES-IN + IN22-Gen + IN22-Conv (real, human-translated,
   already vetted — zero fabrication risk).
2. **Product-specific supplement**: FLORES/IN22 are general-domain and
   under-represent the idiom/tone/cultural-reference cases that are the
   actual differentiator here (the "raining cats and dogs" case). This
   slice has to be curated on purpose, can't be borrowed — probably
   30–50 hand-picked idiom/register/context cases per direction to start.
3. **Score automatically on every pipeline change**: Indic-COMET + chrF++
   as a regression gate (fast, cheap, catches regressions immediately).
4. **Audit a sample with MQM periodically**: human-reviewed, span-level,
   severity-tagged — this is the same review step §6 already calls for,
   now with a named, defensible methodology instead of an ad hoc "looks
   right" check.

## 5. Direct tie to the product's output quality bar (added 2026-08-03)

`CONCEPT.md` §7 states three required properties of every translation:
common/contemporary terminology (not archaic), correct grammar, and
concise (not verbose) phrasing. These map onto three of MQM's eight
dimensions exactly — **Terminology**, **Fluency**, and **Style** — so
the golden-standard scoring pipeline doesn't need a separate rubric for
"is this shuddh Hindi nobody says" versus "is the MQM score good": one
MQM pass, tagged by dimension, answers both. Worth building the human
audit template with these three dimensions surfaced first/prominently,
since they're the ones with an explicit product requirement behind them
— the other five MQM dimensions (Accuracy, Locale convention, Verity,
Design, Internationalization) still apply but weren't called out as
must-haves the way these three were.

## Decided: Hindi ↔ English first (2026-08-02)

Confirmed — both directions, Gemini path only (no Sarvam needed for this
pair). Hindi↔English is the best-covered pair across every dataset
above (FLORES-IN, IN22, Bhashini/AIKosh), so the golden set for v1 can
be assembled without any gaps needing to be filled by hand beyond the
idiom/tone supplement in §4.2. A second, lower-resource language (e.g.
Tamil or Bengali) should follow once Sarvam enters the picture, to prove
that path isn't just riding on Hindi's relatively well-resourced case —
not yet scheduled.

## References

- [AI4Bharat/IndicTrans2 (GitHub)](https://github.com/AI4Bharat/IndicTrans2)
- [IndicTrans2 paper (OpenReview)](https://openreview.net/forum?id=vfT4YuzAYA)
- [IndicTrans2 paper (arXiv)](https://arxiv.org/pdf/2305.16307)
- [AI4Bharat Machine Translation area](https://ai4bharat.iitm.ac.in/areas/nmt)
- [National Mission on Natural Language Translation (Bhashini) — IndiaAI](https://indiaai.gov.in/missions/national-mission-on-natural-language-translation-bhashini)
- [Bhashini Data Management Unit report (AI4Bharat, PDF)](https://indicnlp.ai4bharat.org/static/documents/DMU_Data_Report_May_2022.pdf)
- [AIKosh — Hindi to Kannada Translation Benchmark Dataset](https://aikosh.indiaai.gov.in/home/datasets/details/hindi_to_kannada_translation_benchmark_dataset.html)
- [AIKosh — English to Sindhi Translation Benchmark Dataset](https://aikosh.indiaai.gov.in/home/datasets/details/english_to_sindhi_translation_benchmark_dataset.html)
- [Sarvam Translate (Sarvam AI blog)](https://www.sarvam.ai/blogs/sarvam-translate)
- [Sarvam-1: The first Indian language LLM](https://www.sarvam.ai/blogs/sarvam-1)
- [FLORES-200 overview](https://www.emergentmind.com/topics/flores-200-benchmark-dataset)
- [Flores+ Benchmark overview](https://www.emergentmind.com/topics/flores-benchmark)
- [CFILT, IIT Bombay](https://www.cfilt.iitb.ac.in/)
- [IIT Bombay English-Hindi Parallel Corpus](https://www.cfilt.iitb.ac.in/iitb_parallel/)
- [CFILT-IITB WMT23 IndicMT shared task paper](https://aclanthology.org/2023.wmt-1.89/)
- [Pushpak Bhattacharyya (Wikipedia)](https://en.wikipedia.org/wiki/Pushpak_Bhattacharyya)
- [Multidimensional Quality Metrics (MQM) overview](https://sites.middlebury.edu/runyul/2018/03/04/translation-quality-assessment-mqm-multidimensional-quality-metrics/)
- [Human evaluation metrics — Machine Translate](https://machinetranslate.org/human-evaluation-metrics)
- [IndicMT Eval: meta-evaluating MT metrics for Indian languages (ACL Anthology)](https://aclanthology.org/2023.acl-long.795/)
- [IndicMT Eval paper (arXiv)](https://arxiv.org/pdf/2212.10180)
