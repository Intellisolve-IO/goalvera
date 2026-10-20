/**
 * Local fixture data layer.
 * Shape mirrors Firestore collections (/teachers, /students, /recordings)
 * so a later swap to Firestore clients is a drop-in behind these helpers.
 */

import uiFixtures from '@samples/ui_fixtures.json';
import sampleAnalysis from '@samples/expected_gemini_output_student_a_thu.json';

import { authFetch } from './auth.js';

const STORAGE_KEY = 'ocr_hitl_session_overrides_v1';
const SPEAKER_MAP_KEY = 'ocr_speaker_map_v1';

// Sessions produced by the cloud pipeline (), served read-only by
// app/backend-pipeline/review_api via the Vite /api proxy. If the read
// service is not running, the UI falls back to the sample fixtures only.
let pipelineSessions = [];

export async function loadPipelineSessions() {
  try {
    const res = await authFetch('/api/sessions', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    pipelineSessions = body.sessions || [];
    return { ok: true, count: pipelineSessions.length };
  } catch {
    pipelineSessions = [];
    return { ok: false, count: 0 };
  }
}

/** Recordings still processing () -> {uploads: [{recording_id, state, label, error_text, ...}], processing} */
export async function loadUploadsStatus() {
  const res = await authFetch('/api/uploads/status', { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function getPipelineSessions() {
  return pipelineSessions.map(hydrateSession);
}

export async function fetchTranscript(recordingId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/transcript`, {
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Speaker label -> person mapping is done by a human in review, kept in this browser only. */
export function getSpeakerMap(recordingId) {
  try {
    return JSON.parse(localStorage.getItem(SPEAKER_MAP_KEY) || '{}')[recordingId] || {};
  } catch {
    return {};
  }
}

export function setSpeakerMap(recordingId, map) {
  let all = {};
  try {
    all = JSON.parse(localStorage.getItem(SPEAKER_MAP_KEY) || '{}');
  } catch {
    all = {};
  }
  all[recordingId] = map;
  localStorage.setItem(SPEAKER_MAP_KEY, JSON.stringify(all));
}

function loadOverrides() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveOverrides(overrides) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
}

function hydrateSession(session) {
  const overrides = loadOverrides();
  const ov = overrides[session.recording_id] || {};

  // Pipeline sessions: approval lives on the server (who + when, audit trail).
  // Once approved, the server's approved notes are the record; local edits only
  // apply while the draft is still pending review.
  if (session.source === 'pipeline') {
    const approved = session.status === 'approved';
    return {
      ...session,
      processed_analysis:
        !approved && ov.processed_analysis ? ov.processed_analysis : session.processed_analysis,
      approved_at: session.approved_at || null,
    };
  }

  let analysis = session.processed_analysis;
  if (!analysis && session.fixture_analysis === 'expected_gemini_output_student_a_thu.json') {
    analysis = sampleAnalysis;
  }

  if (ov.processed_analysis) {
    analysis = ov.processed_analysis;
  }

  return {
    ...session,
    status: ov.status || session.status,
    processed_analysis: analysis,
    approved_at: ov.approved_at || null,
  };
}

export function getTeacher() {
  return uiFixtures.teacher;
}

export function getStudents() {
  return uiFixtures.students;
}

export function getDays() {
  return uiFixtures.days;
}

export function getSessions() {
  return uiFixtures.sessions.map(hydrateSession);
}

export function getSession(recordingId) {
  return (
    [...getSessions(), ...getPipelineSessions()].find((s) => s.recording_id === recordingId) ||
    null
  );
}

export function getStudent(studentId) {
  return uiFixtures.students.find((s) => s.student_id === studentId) || null;
}

export function updateSessionAnalysis(recordingId, processed_analysis) {
  const overrides = loadOverrides();
  overrides[recordingId] = {
    ...(overrides[recordingId] || {}),
    processed_analysis,
  };
  saveOverrides(overrides);
}

/** Approve: pending_review → approved. NO email send. */
export function approveSession(recordingId, processed_analysis) {
  const overrides = loadOverrides();
  overrides[recordingId] = {
    ...(overrides[recordingId] || {}),
    status: 'approved',
    processed_analysis,
    approved_at: new Date().toISOString(),
  };
  saveOverrides(overrides);
}

async function apiError(res) {
  let code = `HTTP ${res.status}`;
  try {
    code = (await res.json()).error || code;
  } catch {
    /* not JSON */
  }
  return new Error(code);
}

/** Approve a pipeline session on the review service (a person approves; nothing is sent). */
export async function approvePipelineSession(recordingId, processed_analysis, reviewerId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/approve`, {
    method: 'POST',
    // Hosted: the server takes the reviewer from the sign-in token and ignores this header.
    headers: { 'Content-Type': 'application/json', ...(reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}) },
    body: JSON.stringify({ analysis: processed_analysis }),
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** Move a pipeline session to another classroom (logged on the server: who, when, from, to). */
export async function changePipelineClassroom(recordingId, classCode, reviewerId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/classroom`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}) },
    body: JSON.stringify({ class_code: classCode }),
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/**
 * Download the approved-notes PDF. The server renders it on demand (never
 * stored) and records the download in the audit trail. The file goes only to
 * this browser's downloads — no email, no other system.
 */
export async function downloadApprovedPdf(recordingId, reviewerId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/pdf`, {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {},
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = m ? m[1] : `approved-notes_${recordingId}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * One incident's report for the print view (Download PDF on an incident).
 * The server checks who may export it, logs the click (audit trail + journal, ids
 * only) and returns only what may be printed; the browser makes the PDF.
 */
export async function requestIncidentPrint(recordingId, number, reviewerId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/incident_print`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}) },
    body: JSON.stringify({ number }),
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** First view: the server names incidents that have none yet (AI, scrubbed, observed behavior
 * only; review_api/incident_names.py) -> [{index, number, name, name_source, title}]. */
export async function fetchIncidentNames(recordingId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/incident_names`, {
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return (await res.json()).incidents || [];
}

/** A teacher's Bug marker () -> {status: "filed" | "pending", issue_url, message}. */
export async function submitBugReport(recordingId, payload, reviewerId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/bug_report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}) },
    body: JSON.stringify(payload),
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function fetchAudit(recordingId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/audit`, {
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return (await res.json()).events || [];
}

export function resetOverrides() {
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(SPEAKER_MAP_KEY);
}

// ---------------------------------------------------------------- weekly lesson planner ()

/** Last week (or an earlier one: past=YYYY-MM-DD Monday), this week and next week for a classroom. */
export async function loadPlanner(classCode, past, reviewerId) {
  const q = new URLSearchParams();
  if (classCode) q.set('class_code', classCode);
  if (past) q.set('past', past);
  const res = await authFetch(`/api/planner?${q}`, {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {},
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** Save a new version of a plan / weekly summary / teacher note. A refusal for quoting a
 *  transcript comes back as an Error with .code QUOTES_TRANSCRIPT and .match (the 8 words). */
export async function savePlanner(what, body, reviewerId) {
  const res = await authFetch(`/api/planner/${what}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let j = {};
    try {
      j = await res.json();
    } catch {
      /* not JSON */
    }
    const e = new Error(j.error || `HTTP ${res.status}`);
    e.code = j.error;
    e.match = j.match;
    throw e;
  }
  return res.json();
}

// ---- Students / Teachers / Settings tabs. Read-only fetches.
async function getJson(url, reviewerId) {
  const res = await authFetch(url, {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {},
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** Every roster student (all roles), with the viewer's own classrooms for the default filter. */
export function loadStudents(reviewerId) {
  return getJson('/api/students', reviewerId);
}

/** One student: IEP, one school week of daily summaries, the week's summary / parent email.
 *  Opening it is journaled on the server (iep_viewed). */
export function loadStudent(studentId, week, reviewerId) {
  const q = week ? `?week=${encodeURIComponent(week)}` : '';
  return getJson(`/api/students/${encodeURIComponent(studentId)}${q}`, reviewerId);
}

export function loadTeachers(reviewerId) {
  return getJson('/api/teachers', reviewerId);
}

/** Admins only (403 for everyone else). */
export function loadSettings(reviewerId) {
  return getJson('/api/admin/settings', reviewerId);
}

// ---------------------------------------------------------------- classroom materials ()
// Teacher Notes -> up to 3 suggested materials -> the teacher picks and adjusts -> a printable PDF,
// downloadable here and (school setting) emailed to the teacher's own staff address. The server
// decides who may do what, masks names before any AI call and logs every step.

function jsonHeaders(reviewerId) {
  return { 'Content-Type': 'application/json', ...(reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}) };
}

/** -> {can_make, notes: [{index, offset_seconds, is_request, suggestions}], students, types, materials, email} */
export async function fetchMaterials(recordingId, reviewerId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/materials`, {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {},
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** body: {note_index, type, count, topic, student_ids, due, regenerate_of} -> {material (3 drafts), email} */
export async function makeMaterial(recordingId, body, reviewerId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/materials`, {
    method: 'POST', headers: jsonHeaders(reviewerId), body: JSON.stringify(body), cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function resendMaterial(recordingId, materialId, reviewerId) {
  const res = await authFetch(
    `/api/sessions/${encodeURIComponent(recordingId)}/materials/${encodeURIComponent(materialId)}/resend`,
    { method: 'POST', headers: jsonHeaders(reviewerId), body: '{}', cache: 'no-store' },
  );
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** Photo-style flashcards: ask the server to make the next picture (paced there, ~2 a minute). */
export async function continueMaterial(recordingId, materialId, reviewerId) {
  const res = await authFetch(
    `/api/sessions/${encodeURIComponent(recordingId)}/materials/${encodeURIComponent(materialId)}/continue`,
    { method: 'POST', headers: jsonHeaders(reviewerId), body: '{}', cache: 'no-store' },
  );
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** The PDF is rendered on demand by the server (never stored) and saved to this browser's downloads. */
export async function downloadMaterial(recordingId, materialId, reviewerId, draft = null) {
  const q = draft ? `?draft=${encodeURIComponent(draft)}` : '';
  const res = await authFetch(
    `/api/sessions/${encodeURIComponent(recordingId)}/materials/${encodeURIComponent(materialId)}/pdf${q}`,
    { headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}, cache: 'no-store' },
  );
  if (!res.ok) throw await apiError(res);
  const blob = await res.blob();
  const m = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = m ? m[1] : `materials_${materialId}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function loadMaterialsEmail(reviewerId) {
  const res = await authFetch('/api/admin/materials_email', {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}, cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function saveMaterialsEmail(enabled, reviewerId) {
  const res = await authFetch('/api/admin/materials_email', {
    method: 'POST', headers: jsonHeaders(reviewerId), body: JSON.stringify({ enabled }), cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** The system instruction for SVG drafts: everyone who makes materials reads it; admins edit / reset. */
export async function loadMaterialsInstruction(reviewerId) {
  const res = await authFetch('/api/admin/materials_instruction', {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}, cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export async function saveMaterialsInstruction(body, reviewerId) {
  const res = await authFetch('/api/admin/materials_instruction', {
    method: 'POST', headers: jsonHeaders(reviewerId), body: JSON.stringify(body), cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

// ---------------------------------------------------------------- Domains + Classes ()
// Domains: Subject / Domain -> core methodology, programs, book (educational methodology document).
// Classes: name, teacher(s), domain, students, classroom, schedule. Everyone signed in reads both; the
// server decides who may change what (admins; a teacher on their own class) and journals each change.
// Every save sends the `version` it was edited from; 409 STALE_VERSION means someone saved first.

export function loadDomains(reviewerId) {
  return getJson('/api/domains', reviewerId);
}

export function loadClasses(reviewerId) {
  return getJson('/api/classes', reviewerId);
}

async function postJson(url, body, reviewerId) {
  const res = await authFetch(url, {
    method: 'POST', headers: jsonHeaders(reviewerId), body: JSON.stringify(body), cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** id null: add. remove: delete. -> the new domains view */
export function saveDomain({ id = null, domain = null, version, remove = false }, reviewerId) {
  const url = id ? `/api/domains/${encodeURIComponent(id)}${remove ? '/delete' : ''}` : '/api/domains';
  return postJson(url, remove ? { version } : { version, domain }, reviewerId);
}

/** id null: add. remove: delete. -> the new classes view */
export function saveClass({ id = null, klass = null, version, remove = false }, reviewerId) {
  const url = id ? `/api/classes/${encodeURIComponent(id)}${remove ? '/delete' : ''}` : '/api/classes';
  return postJson(url, remove ? { version } : { version, class: klass }, reviewerId);
}

/** -> {class (picked, else suggested), source: teacher|suggested|cleared|null, suggested_id, choices, can_change} */
/** Speaker selector choices (): the class's people first, then everyone, then generic options. */
export function loadSpeakers(recordingId, reviewerId) {
  return getJson(`/api/sessions/${encodeURIComponent(recordingId)}/speakers`, reviewerId);
}

/** Weekly parent email (): preview (recipients, excluded contacts, exact text, demo redirect). */
export function loadParentEmail(studentId, week, reviewerId) {
  return getJson(`/api/students/${encodeURIComponent(studentId)}/parent_email?week=${encodeURIComponent(week)}`, reviewerId);
}

/** One teacher click: sends exactly the previewed emails (preview_sha256). */
export function sendParentEmail(studentId, week, previewSha256, reviewerId) {
  return postJson(`/api/students/${encodeURIComponent(studentId)}/parent_email/send`,
    { week, preview_sha256: previewSha256 }, reviewerId);
}

/** Admin: demo redirect for parent emails. */
export function loadParentEmailSetting(reviewerId) {
  return getJson('/api/admin/parent_email', reviewerId);
}

export function saveParentEmailSetting(redirect, reviewerId) {
  return postJson('/api/admin/parent_email', { redirect }, reviewerId);
}

/** Students in this session () -> {student_ids, saved, suggestion, choices, others, max, can_change} */
export function loadSessionStudents(recordingId, reviewerId) {
  return getJson(`/api/sessions/${encodeURIComponent(recordingId)}/students`, reviewerId);
}

export function saveSessionStudents(recordingId, studentIds, reviewerId) {
  return postJson(`/api/sessions/${encodeURIComponent(recordingId)}/students`, { student_ids: studentIds }, reviewerId);
}

export function loadSessionClass(recordingId, reviewerId) {
  return getJson(`/api/sessions/${encodeURIComponent(recordingId)}/class`, reviewerId);
}

export function saveSessionClass(recordingId, classId, reviewerId) {
  return postJson(`/api/sessions/${encodeURIComponent(recordingId)}/class`, { class_id: classId || null }, reviewerId);
}

// ---------------------------------------------------------------- lesson plan materials ()
// "Create worksheet" on a week of the Lesson planner, and "Add to lesson plan" on a session's
// materials. Same Make flow, instructions, privacy rules and journal as the session materials.

async function postPlanJson(url, body, reviewerId) {
  const res = await authFetch(url, {
    method: 'POST', headers: jsonHeaders(reviewerId), body: JSON.stringify(body || {}), cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** -> {can_make, students, types, classes, email, weeks: {<monday>: [materials]}} */
export async function fetchPlanMaterials(classCode, weeks, reviewerId) {
  const q = new URLSearchParams({ class_code: classCode, weeks: weeks.join(',') });
  const res = await authFetch(`/api/planner/materials?${q}`, {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}, cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

/** body: {class_code, week, lesson: {day, label}, class_id?, type, style?, count, topic, student_ids, due, regenerate_of?} */
export function makePlanMaterial(body, reviewerId) {
  return postPlanJson('/api/planner/materials', body, reviewerId);
}

/** what: resend | continue | remove; where: {class_code, week} */
export function planMaterialAction(materialId, what, where, reviewerId) {
  return postPlanJson(`/api/planner/materials/${encodeURIComponent(materialId)}/${what}`, where, reviewerId);
}

export async function downloadPlanMaterial(materialId, where, reviewerId, draft = null) {
  const q = new URLSearchParams({ class_code: where.class_code, week: where.week, ...(draft ? { draft: String(draft) } : {}) });
  const res = await authFetch(`/api/planner/materials/${encodeURIComponent(materialId)}/pdf?${q}`, {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {}, cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  const blob = await res.blob();
  const m = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = m ? m[1] : `materials_${materialId}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A session material -> its classroom's lesson plan (default: the session's week). */
export function attachMaterial(recordingId, materialId, body, reviewerId) {
  return postPlanJson(
    `/api/sessions/${encodeURIComponent(recordingId)}/materials/${encodeURIComponent(materialId)}/attach`,
    body, reviewerId,
  );
}

// My profile: the signed-in user's own profile. Role and email are
// read-only unless the server's PROFILE_SELF_EDIT_ACCESS flag is on (can_edit_access).
export function loadMyProfile() {
  return getJson('/api/profile/me');
}

export function saveMyProfile(fields) {
  return postJson('/api/profile/me', fields);
}

/** Admins (Teachers tab): a person's app role (admin / teacher / rbt / none) and/or sign-in email. */
export function changeAccess({ person = null, currentEmail = null, email = null, role = null }) {
  return postJson('/api/admin/people/access', { person, current_email: currentEmail, email, role });
}

// Teacher Notes photo / video (): fetched with the sign-in header, shown from a blob: URL.
export async function fetchMediaUrl(recordingId, mediaId, reviewerId) {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(recordingId)}/media/${encodeURIComponent(mediaId)}`, {
    headers: reviewerId ? { 'X-Reviewer-Id': reviewerId } : {},
    cache: 'no-store',
  });
  if (!res.ok) throw await apiError(res);
  return URL.createObjectURL(await res.blob());
}
