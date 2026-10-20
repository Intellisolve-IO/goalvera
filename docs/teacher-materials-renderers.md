# Classroom materials v2: one request flow, three renderers ()

Builds on the SVG drafts and the scope in #26 / #28.

## What the teacher sees

Nothing new to learn. It's the same Teacher Notes card and the same **Make** form for every subject (math,
English, science, ...): type, how many, topic, student(s), due date. One new field shows for
flashcards: **Style**, which is *Line art*, *Color* or *Photo-style (slow)*. The AI's suggestions on a Teacher
Note now pick the type, and the style for flashcards, so routing usually needs no extra click.

## Routing: activity type → renderer

| Activity type | Renderer | Drafts | What the AI does |
|---|---|---|---|
| Coloring page, Classroom labels, Worksheet, Diagram / life cycle, Flashcards (line art) | **SVG line art**, gemini-3.8-flash | 3 | Infers the request, then writes 3 SVG pages under that type's instruction |
| Flashcards (color) | **SVG color**, gemini-3.8-flash | 3 | Same, under a separate *color* instruction (flat, bright cartoon color) |
| Multiplication table, Number line, Letter tracing | **Code template** | 3 layouts | Fills structured parameters only (range, start/end/step, words). The app computes every number and the whole layout itself, so no AI arithmetic ends up on the page |
| Flashcards (photo-style) | **Image model**, gemini-3.1-flash-lite-image (global; a setting) | 1 | Picks the card words and one picture prompt per card. Pictures are made one at a time, paced under the ~2/min quota |

All three renderers end the same way. The app puts the school header on (school name from the setting, blank
Name and Date lines, never a student name). It renders PNG previews and the PDF on the server, and sends the
same email to the teacher's own address. Model SVG stays untrusted and goes through the sanitizer. Templates
and the photo layout are built by our own code.

### Photo-style flashcards (slow path)

The image model allows about 2 pictures a minute, and a Cloud Run request has 120 s. So a photo set is
**made in steps**:
1. **Make** returns at once with status *Making pictures: may take a few minutes (about N min)*.
2. While the session is open, the page asks the server to continue about every 15 s.
3. Each continue makes at most one picture, at most one every 35 s per set, retrying a 429 on the next
   turn. Each picture is stored as its own create-only object.
4. When the last picture is in, the set is finished: previews, Download, and the email (sent once, guarded
   by a create-only "done" marker).

Pages hold 4 big cards each (2 x 2), so a picture prints about 3.3 in wide and 8 cards take 2 pages. The
cards have dashed borders for cutting. (Changed Oct 10 from 8 small cards on one page.)

If the teacher closes the session, the set pauses and carries on when it's opened again. Cards without a
picture yet show an empty frame, never a broken page.

The flashcard **Style** also goes to the planning step (`style` plus a short `style_label` in the payload). That
way the 3 drafts' request and design ideas stay in the chosen style, e.g. no "photo-style" or outline-only drafts in a
color set.

## Instructions (admin, Settings)

Each activity type has its own instruction. Admins edit it or reset it to its default; teachers see it
read-only. Every change is logged as `settings_changed`, setting `materials_instruction`, with the
instruction key, who, when, length and hash (not the text).

| Instruction key | Default |
|---|---|
| coloring, labels, worksheet, diagram, flashcards_line | The default SVG instruction (verbatim) |
| flashcards_color | The default instruction with the visual style changed to flat, bright cartoon color |
| multiplication_table, number_line, letter_tracing | Short JSON-parameter instructions (ranges, words, grade fit) |
| flashcards_photo | The picture style for the image model (photo-like, plain background, one subject, no text or people's faces) |

An instruction that contains a roster name or an email address is refused.

## Domains / Classes hook

A separate job is building the Domains/Classes module, so this work does **not** build it. `Materials`
takes an optional `domain_context(session) -> str` provider. When it returns text (the class's domain or
methodology), the text is masked, checked for names, emails and ids like everything else, capped at 1,500
characters, and added to the AI context as `class_domain`. Until then it's empty.

Connected (): the review service passes `SchoolSetup.domain_context`, which returns
"<domain>: <core methodology>" for the session's class (the one the teacher picked on the session page,
else the one suggested from the classroom + start time), e.g. "Mathematics: Concrete-Pictorial-Abstract
(CPA) Conceptual math instruction ...". The SVG prompt gets it as a `Teaching Approach:` line between
Activity Type and Draft Variation; the brief and parameter prompts are told to fit the page to it
(BRIEF / PARAMS v2). Never sent: the class name, programs, book, schedule or student list. The make form
shows the teacher which approach the AI will design for.

## Privacy and logging (unchanged)

- Names are masked before any AI call, and the output is scrubbed again.
- Email goes only to the teacher's own staff address, with first names only and no IEP text.
- Everything is journaled. Fictional data only.
- New journal details: renderer, style and instruction key, plus for photo sets the number of pictures made
  and missing.

## Estimate (bot hours, low / likely / high)

| Piece | Hours |
|---|---|
| Plan, types, routing, suggestions, per-type instructions (backend + Settings UI) | 1.5 / 2 / 3 |
| Code templates: multiplication table, number line, letter tracing (3 layouts each) | 1.5 / 2 / 3 |
| SVG color style + diagram type | 0.5 / 0.75 / 1 |
| Photo flashcards: paced continue steps, done marker, email on finish, status UI | 1.5 / 2 / 3 |
| Domain hook, tests (backend + frontend), CI, merge, deploy, 3 samples | 1.5 / 2 / 3 |
| **Total** | **6.5 / 8.75 / 13** |

## Cost per request (Vertex list prices, Oct 2026; doubles for the text model from Jan 1, 2027)

| Type | Per request | Notes |
|---|---|---|
| SVG line art (3 drafts) | ~$0.05 | ~550 in / ~4,200 out tokens per draft on gemini-3.8-flash |
| SVG color flashcards (3 drafts) | ~$0.06 | color pages run a little longer |
| Code templates (3 layouts) | ~$0.001 | one small parameter call; layout is free |
| Photo flashcards, 8 cards | ~$0.28 | 8 × ~$0.034 per picture + ~$0.002; about 4-5 minutes |

At demo volumes (a few dozen requests a month) that's well under $5/month of AI. Photo sets are the cost driver
and are opt-in per request.

## Settings and files

- `MATERIALS_IMAGE_MODEL` (default gemini-3.1-flash-lite-image) and `MATERIALS_IMAGE_LOCATION` (global) sit
  next to `MATERIALS_SVG_MODEL` / `_LOCATION` in infra/config.sh. `MATERIALS_PHOTO_GAP` (35 s) sets the pacing.
- Code: `material_ai.renderer()` routes requests; `material_templates.py` holds the code templates and the photo
  card layout; `materials.Materials.continue_()` runs the paced photo steps; the API route is
  `POST /api/sessions/<id>/materials/<mid>/continue`.
- Instructions are stored as `materials_settings/instruction-<key>-<time>-<x>.json`. None existed before this
  change, so nothing needs migrating.
