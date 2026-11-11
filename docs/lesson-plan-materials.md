# Worksheets in the Lesson planner

## What the teacher sees

- Every week in the Lesson planner has a **Worksheets & materials** section between the plan and the
  students. **+ Create worksheet** opens the same Make form as a session's Teacher Notes, with these fields:
  - day (Mon–Fri, optional)
  - lesson name (optional)
  - class (optional; its subject and teaching approach guide the AI)
  - type, plus style for flashcards
  - count
  - details (topic)
  - up to 3 students (optional)
- What's made is attached to that week's lesson plan. From there the teacher can preview it, Download it, Regenerate it
  (the new version replaces the old one in the plan), Remove it, and Email it to themselves.
- On a session's Materials card, **Add to lesson plan** copies a material made from a Teacher Note into
  that classroom's plan for the session's week. The card then says "In the lesson plan · week of …".

## Rules (unchanged)

- It uses the same renderers (SVG line or color, code templates, paced photo flashcards), the same per-type admin
  instructions, the same name masking and checks, the same journal and the same email-to-teacher setting.
- Planner materials have no transcript, so the AI gets the teacher's form plus de-identified student context.
  The only class context is the picked class's domain name and core methodology.
- Who may act follows the planner: the classroom's teacher or an admin. RBTs are read-only.
- Journal entries are materials_requested / made / downloaded / emailed with record_type plan, the classroom key
  and the week. New entries: materials_attached (with the source recording id) and materials_removed.

## Storage (lesson plans already persist: planner/plans/<class_key>/<week>/, #76 /)

    planner/materials/<class_key>/<week>/<material>.json          the material (same format as session ones,
                                                                  plus place, lesson, class_id, attached_from)
    planner/materials/<class_key>/<week>/<material>.pic-NN.json   photo cards
    planner/materials/<class_key>/<week>/<material>.done.json
    planner/materials/<class_key>/<week>/<material>.send-<t>.json one email attempt
    planner/materials/<class_key>/<week>/<material>.removed.json  taken off the plan (never deleted)

These files are kept with the plan: they're not on the 30-day materials/ rule. Like everything else under planner/, they're
removed when the school's data is deleted, by infra/delete_planner.py. All writes are create-only. A session material added to a
plan is copied, pictures included, so it survives after the session's own materials/ copy expires.

## API

    GET  /api/planner/materials?class_code=..&weeks=w1,w2,w3
    POST /api/planner/materials                      {class_code, week, lesson, class_id?, type, ...}
    GET  /api/planner/materials/<mid>/pdf?class_code&week[&draft]
    POST /api/planner/materials/<mid>/(resend|continue|remove)   {class_code, week}
    POST /api/sessions/<rid>/materials/<mid>/attach  {week?, lesson?}
