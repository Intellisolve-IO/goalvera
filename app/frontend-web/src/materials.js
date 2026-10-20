// Classroom materials from Teacher Notes (): small helpers shared by the panel and its tests.

export const UNIT = {
  worksheet: 'question', coloring: 'picture', flashcards: 'card', labels: 'label', diagram: 'stage',
  multiplication_table: 'row', number_line: 'mark', letter_tracing: 'line',
};
export const STYLE_LABEL = { line: 'line art', color: 'color', photo: 'photo-style' };
export const POLL_MS = 15000;

export function plural(n, unit) {
  return `${n} ${unit}${n === 1 ? '' : 's'}`;
}

function iso(d) {
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** "today" / "tomorrow" / "YYYY-MM-DD" / "" -> a date for the form ("" = none). */
export function dueDate(due, now = new Date()) {
  if (due === 'today') return iso(now);
  if (due === 'tomorrow') {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    return iso(d);
  }
  return /^\d{4}-\d{2}-\d{2}$/.test(due || '') ? due : '';
}

export function dueLabel(dateStr) {
  if (!dateStr) return '';
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

export function mmss(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const TYPE_LABEL = {
  worksheet: 'Worksheet', coloring: 'Coloring page', flashcards: 'Flashcards', labels: 'Classroom labels',
  diagram: 'Diagram / life cycle', multiplication_table: 'Multiplication table', number_line: 'Number line',
  letter_tracing: 'Letter tracing',
};

/** The words on a suggestion button: "Flashcards · 8 cards · farm animals · for tomorrow". */
export function suggestionText(s) {
  const label = TYPE_LABEL[s.type] || s.type;
  const parts = [s.type === 'flashcards' && s.style && s.style !== 'line' ? `${label} (${STYLE_LABEL[s.style]})` : label];
  if (s.type === 'multiplication_table') parts.push(`up to ${s.count}`);
  else parts.push(plural(s.count, UNIT[s.type] || 'item'));
  if (s.topic) parts.push(s.topic);
  if (s.due === 'today' || s.due === 'tomorrow') parts.push(`for ${s.due}`);
  else if (s.due) parts.push(`for ${dueLabel(s.due)}`);
  return parts.join(' · ');
}

const EMAIL_STATUS = {
  emailed: 'Emailed',
  email_off: 'Not emailed: email delivery is off',
  no_staff_email: 'Not emailed: no staff email on file',
  mail_not_set_up: 'Not emailed: email isn’t set up yet',
  mail_needs_reapproval: 'Not emailed: email needs to be approved again',
  recipient_domain_not_allowed: 'Not emailed: that address isn’t allowed',
  send_failed: 'Not emailed: sending failed',
};

export function emailStatusText(status, to = null) {
  if (status === 'emailed') return to ? `Emailed to ${to}` : 'Emailed';
  return EMAIL_STATUS[status] || (status ? `Not emailed (${status})` : '');
}

/** The count's max for the chosen type and style (photo-style sets are smaller). */
export function countMax(tinfo, style) {
  return style === 'photo' ? Math.min(tinfo.max, tinfo.photo_max || tinfo.max) : tinfo.max;
}

/** "Making pictures: may take a few minutes (2 of 8 done, about 4 min left)". */
export function makingText(m) {
  const left = m.eta_minutes ? `, about ${m.eta_minutes} min left` : '';
  return `Making pictures: may take a few minutes (${m.pictures_made || 0} of ${m.pictures_total || 0} done${left}). Keep this session open.`;
}

/** Form state from a suggestion (or a made material, for Regenerate). */
export function formFrom(s, view, extra = {}) {
  const t = (view.types || []).find((x) => x.type === s.type) || { min: 1, max: 5, default: 1 };
  const style = s.type === 'flashcards' ? (s.style || 'line') : undefined;
  const max = style === 'photo' ? Math.min(t.max, t.photo_max || t.max) : t.max;
  const count = Math.min(max, Math.max(t.min, Number(s.count) || t.default));
  return {
    type: s.type,
    ...(style ? { style } : {}),
    count,
    topic: s.topic || '',
    student_ids: s.student_ids || view.default_student_ids || [],
    due: s.due && s.due.length === 10 ? s.due : dueDate(s.due),
    ...extra,
  };
}
