# Goalvera: native Google Cloud agents (living design doc)

Status: **design only, no code yet.** Last updated: 2026-10-20. Open decisions are in section 8.

## 0. Product names checked (Oct 2026)

Google renamed several products at Cloud Next 2026. We use the current names below.

| What we need | Current name / status | Source |
|---|---|---|
| Agent framework | **Agent Development Kit (ADK)**, Python, open source (`google/adk-python`) | [ADK agent on Agent Runtime](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime/create-an-adk-agent) |
| Hosting | **Agent Runtime** on **Gemini Enterprise Agent Platform** (was *Vertex AI Agent Engine*; same APIs/SDK, `vertexai.agent_engines.AdkApp`) | [Scale your agents](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale), [release notes](https://docs.cloud.google.com/gemini-enterprise-agent-platform/release-notes) |
| Agent registry | **Agent Registry**, a standalone catalog of agents, MCP servers and endpoints. Agents deployed to Agent Runtime with the SDK are **auto-registered in the same project**; regional registries match the deploy region. Gemini Enterprise (the end-user app) can *import* agents from Agent Registry; we don't need that app. | [Register agents](https://docs.cloud.google.com/agent-registry/register-agents), [Gemini Enterprise import](https://docs.cloud.google.com/gemini/enterprise/docs/import-govern-agent-registry) |
| Evaluation | **Gen AI Evaluation Service** (`google-cloud-aiplatform[evaluation]`): pointwise and side-by-side, custom rubric metrics, agent trace metrics | [Agent Platform SDK](https://docs.cloud.google.com/python/docs/reference/agentplatform/latest) |
| Grounding | **Vertex AI RAG Engine**; ADK tool `vertex_ai_rag_retrieval` (note: it must be the only tool on its agent, so wrap it as a sub-agent) | [ADK RAG Engine tool](https://github.com/google/adk-docs/blob/main/docs/integrations/vertex-ai-rag-engine.md) |
| Tracing | Agent Observability GA (June 18, 2026); OpenTelemetry tracing on by default for ADK agents | [release notes](https://docs.cloud.google.com/gemini-enterprise-agent-platform/release-notes) |
| Models | **Gemini 4 Argon** (`gemini-4-argon`) went **GA Oct 13, 2026** (Gemini API + Vertex; `global`, `us`, `eu` endpoints). Gemini 3.x stays available. **Decided (Oct 20):** use **us-east1** if Argon is offered there; otherwise the `us` multi-region endpoint (agents run in the background, so latency isn't a concern). | [Google blog](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-argon/), [Cloud SKU](https://cloud.google.com/skus/sku-groups/gen-ai-gemini-4-argon) |

Note: the Agent Registry is a discovery and governance catalog. It doesn't run comparisons. Our router (section 4) runs side-by-side tests.

## 1. Pilot scope and go-live gates

- The first deployment mode is **one-on-one sessions with a single consented student.**
- The multi-student flow (section 3) is designed now but **gated off for real data**: config flag `multi_student_split_enabled`, **default `false`**. While it's off, the review UI allows only one student label per recording.
- **Go-live gate for the multi-student flag:** every student in the room has consent covering group sessions. Synthetic test data can use it before then.

## 2. The three agents

Every agent gets **"the student"** and an internal `student_ref`, never a name (section 7). Every output carries `agent_id`, `agent_version`, `config_id`, `model`, `run_id`.

### 2.1 Session analysis
- **Trigger:** teacher saves/approves the recording in the review UI after labeling speakers (section 3).
- **Inputs:** approved, de-named transcript; `student_ref`; the student's active IEP goals (IDs + text); session metadata (date, length, subject, lesson plan ID).
- **Tools:** read IEP goals; read lesson plan; optional grounding sub-agent (section 6).
- **Grounding:** optional, school-authored method materials only.
- **Output (one per student):**
```json
{
  "student_ref": "stu_123", "session_id": "ses_456",
  "agent_id": "session-analysis", "agent_version": "1.0.0", "config_id": "sa-g31-a", "model": "gemini-3.1-pro", "run_id": "…",
  "summary": "string, 3-5 sentences",
  "goal_evidence": [{"goal_id": "g1", "observation": "string", "transcript_refs": ["00:12:30"], "status": "progress|steady|concern|not_observed"}],
  "skills_practiced": ["string"],
  "teacher_followups": ["string"],
  "grounding_citations": [{"doc_id": "string", "passage": "string"}],
  "confidence": 0.0
}
```

### 2.2 Weekly summary (first side-by-side test)
- **Trigger:** weekly schedule (Fridays 4 PM ET; 3 PM ET ok) per student with at least one approved analysis that week.
- **Inputs:** that week's approved session analyses for one student; IEP goals and baselines; prior weekly summary.
- **Tools:** read analyses; read goals; optional grounding sub-agent.
- **Grounding:** optional, cleared materials only.
- **Output:**
```json
{
  "student_ref": "stu_123", "week_start": "2026-11-02",
  "agent_id": "weekly-summary", "agent_version": "1.0.0", "config_id": "ws-argon-a", "model": "gemini-4-argon", "run_id": "…",
  "benchmark": [{"goal_id": "g1", "sessions_observed": 2, "trend": "up|flat|down|insufficient_data", "evidence": "string", "source_session_ids": ["ses_456"]}],
  "teacher_notes": "string",
  "parent_email_draft": {"subject": "string", "body": "string, plain language, uses 'your child' placeholder"},
  "status": "draft_pending_teacher_approval"
}
```
- The parent email is **always a draft.** A teacher approves or edits before anything goes out. During a side-by-side test, only the output the teacher picks moves on to the parent email.

### 2.3 Student evaluation
- **Trigger:** midterm and quarter end (dates from the school's quarter calendar).
- **Inputs:** all approved analyses and weekly summaries for the period; IEP goals and baselines.
- **Tools:** read history; read goals and calendar; grounding sub-agent (most likely of the three to need it).
- **Rules:** progress notes on IEP goals only. **No ranking, no comparison between students, no diagnosis or guesses at conditions.** Teacher approves.
- **Output:**
```json
{
  "student_ref": "stu_123", "period": "Q2-midterm",
  "agent_id": "student-evaluation", "agent_version": "1.0.0", "config_id": "se-g31-a", "model": "gemini-3.1-pro", "run_id": "…",
  "goal_progress": [{"goal_id": "g1", "baseline": "string", "current": "string", "progress_note": "string", "evidence_session_ids": ["ses_456"]}],
  "recommended_focus": ["string"],
  "grounding_citations": [{"doc_id": "string", "passage": "string"}],
  "status": "draft_pending_teacher_approval"
}
```
- A post-check rejects outputs that contain ranking or diagnostic language and sends them back for regeneration.

## 3. Speaker labeling in the review UI (trigger for session analysis)

The teacher, not a model, decides who said what.

**UI step (review screen):**
1. Teacher opens the recording. Transcript shows diarized speaker turns (Speaker 1, 2, …) with play buttons per turn.
2. A **Speakers** panel lists each detected speaker with a few sample lines and a dropdown: *Teacher*, one of the students on the session roster, or *Other / ignore*.
3. Teacher can fix a single turn's speaker (for diarization mistakes) by clicking the turn.
4. **Save/Approve** is enabled only when every speaker is labeled. Saving locks the mapping and **triggers session analysis once per labeled student.**
5. With the flag off (single-student mode), the roster dropdown holds only the one consented student and only one speaker can be mapped to a student.

**Data model (Firestore):**
```json
recordings/{recording_id}: {
  "session_id": "ses_456", "school_id": "atl-prep", "teacher_id": "t_9",
  "status": "transcribed|labeled_approved|analyzed",
  "roster_student_refs": ["stu_123"]
}
recordings/{recording_id}/speaker_labels/{speaker_id}: {
  "speaker_id": "spk_1", "role": "teacher|student|ignore",
  "student_ref": "stu_123 | null", "labeled_by": "t_9", "labeled_at": "timestamp"
}
recordings/{recording_id}/turn_overrides/{turn_id}: {
  "turn_id": "turn_42", "speaker_id": "spk_2", "changed_by": "t_9", "changed_at": "timestamp"
}
```
- On approve, the app builds one de-named slice per `student_ref` (that student's turns as evidence, teacher turns as context; *ignore* turns dropped) and calls the router.
- Label changes after approval create a new version and re-run analysis; the journal records both.

## 4. Versions, configs and the router

- **Agent Registry** lists each deployed agent (auto-registered on Agent Runtime deploy in a separate tools project, us-east1).
- **Config record** (Firestore, versioned, mirrored in the repo): `config_id`, `agent_id`, `agent_version`, model, prompt version, tools, RAG corpora, temperature.
- **Router** (small Cloud Run service in the app): the app never calls a model directly. Per school and agent it reads:
  - `active_config`
  - optional `side_by_side`: `challenger_config`, start/end dates
- **Run comparison (B) checkbox:** each agent has a per-agent "Run comparison (B)" setting in admin, **off by default for every agent**. When off, the router runs only `active_config` and nothing is doubled. It runs a side-by-side test only when the box is checked and a `challenger_config` is set.
- **Side-by-side test:** the router runs **both** configs on the **same** input (same transcripts, same student), stores both outputs under one `comparison_id`, and logs config/version on each.
- **Rollback:** set `active_config` back to the previous ID. No redeploy. Old versions stay deployed until retired.

## 5. Evaluation

- **Offline test set:** fixed, versioned, **synthetic or de-identified** sessions (start with 30-50), with reference notes written or checked by a teacher.
- **Pointwise metrics (Gen AI Evaluation Service, custom rubrics):** factual accuracy against the transcript; IEP goal coverage; no invented facts; parent-email tone and reading level; pedagogy alignment when grounded; policy check (no names, no ranking/diagnosis).
- **Pairwise metrics:** the Evaluation Service's pairwise (side-by-side) judge compares the two configs' outputs on the same input and records a winner and reason per rubric item. Runs on the offline set and on every live comparison.
- **Teacher side-by-side review:** the review screen shows output A and B side by side (order randomized, config hidden). Teacher picks A, B, or equal, and may edit the pick. **Only the picked output proceeds** to the parent email draft; the other is kept for evaluation only.
- **Promotion rule:** to be set later. Teacher picks are the deciding signal; pairwise results are logged only.
- **First test:** weekly summary, Gemini 4 Argon vs Gemini 3.1.
- Side-by-side tests run the agent under test twice per input (once per config).
- **Decided:** "more effective" = teacher picks (A, B, or equal).

## 6. Domain grounding (RAG): Instructional methodology

- Admin setting: **Instructional methodology**. Upload label: **Methodology documents**, grouped by domain (e.g., reading, math).
- Schools write and upload their own methodology documents. No special segmentation beyond per-school corpora.
- One Vertex AI RAG Engine corpus per school, attached only to agents that need it through a grounding sub-agent (RAG tool must be alone on its agent).
- Outputs cite document name + version + passage.

### 6.1 Methodology document ingestion

- **Upload:** admin uploads PDF or DOCX under a domain. Required checkbox: *"This document was created by our school, or we have permission to use it."* The check is logged with uploader and time; upload is blocked without it.
- **Storage:** the school's own Cloud Storage bucket in the school's own GCP project; the RAG corpus lives there too.
- **Extraction and chunking:** Document AI layout parser extracts text and structure; chunks follow the document's headings (heading-aware chunking).
- **Embedding:** chunks are embedded into the school's Vertex AI RAG Engine corpus.
- **Chunk preview:** admins see how each document was split before and after indexing.
- **Versioning:** every upload is a new immutable version (version id + content hash). Revising a document creates a new version and re-indexes only that document. The prior version is retired from retrieval but retained per the school's records-retention setting; an admin can delete it.
- **Removal:** an admin can retire a document (drops out of retrieval at once) or delete it (source file, chunks and embeddings removed; deletion logged with who and when).
- **Citations:** session analysis, student evaluation and weekly summary cite document name + version + passage used.
- **A/B:** a comparison can pin two methodology versions (same agent, same transcripts) to test a revision.

## 7. Privacy engineering

- Models never see student names: "the student" plus `student_ref`. The app re-inserts names after the agent returns.
- School data (methodology uploads, buckets, RAG corpora) lives in the school's own GCP project, **us-east1** (see §6.1). Agents deploy in a separate tools project.
- Every run, including both sides of a comparison and teacher picks, is logged to the **admin activity journal in BigQuery** (run_id, comparison_id, agent/config/version, model, student_ref, trigger, teacher action).
- **A teacher approves before any parent email is sent.**
- Eval sets use synthetic or de-identified data. Unpicked comparison outputs follow the same deletion schedule.
- Models run in us-east1 when offered there, otherwise the `us` multi-region endpoint.

## 8. Decisions

**Decided:**
- **"More effective"** = the teacher picks A, B, or equal on each side-by-side. The automated pairwise results are logged only, not used to decide. The promotion rule comes later.
- **Parent email** stays a draft for the session teacher to approve or edit.
- **Weekly summary** runs Fridays at 4 PM ET (3 PM ET is also fine).
- **Endpoint:** us-east1 if Gemini 4 Argon is available there, otherwise the `us` multi-region endpoint.
- **Methodology:** schools upload their own methodology documents; no special segmentation.

**Still open:**
1. Templates for the benchmark report and parent email.
2. Which domains and methodology documents the school uploads first.
