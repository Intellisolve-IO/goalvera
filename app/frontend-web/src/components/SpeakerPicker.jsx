import React, { useState } from 'react';

// Who is this speaker? (David, Oct 10 2026; tasks#104). Choices come from the roster (students and staff,
// first name + last initial): the session's class first, then everyone else (type to narrow), then generic
// options. The choice is kept in this browser only and never sent to the AI.
export const FALLBACK_GENERIC = ['Unknown speaker', 'Other adult', 'Other student'];
const MAX_OTHERS = 60;

export function matches(p, q) {
  const s = q.trim().toLowerCase();
  if (!s) return true;
  return [p.label, p.role, p.classroom].some((x) => x && String(x).toLowerCase().includes(s));
}

export function pickerGroups(choices, query = '', current = '') {
  const c = choices || {};
  const inClass = c.in_class || [];
  const found = (c.others || []).filter((p) => matches(p, query));
  const others = query.trim() ? found : found.slice(0, MAX_OTHERS);
  const generic = c.generic && c.generic.length ? c.generic : FALLBACK_GENERIC;
  const shown = new Set([...inClass, ...others].map((p) => p.label).concat(generic));
  return {
    inClass, others, generic, more: found.length - others.length,
    // an earlier choice (e.g. "Teacher" from the old fixed list, or someone filtered out) stays selectable
    keep: current && !shown.has(current) ? current : null,
  };
}

const optText = (p) => (p.kind === 'staff' ? `${p.label} · ${p.role}` : p.label);

export default function SpeakerPicker({ speaker, value = '', choices, onChange, readOnly = false }) {
  const [q, setQ] = useState('');
  const g = pickerGroups(choices, q, value);
  const basis = choices && choices.basis ? choices.basis.name : null;
  return (
    <div className="speaker-picker" data-testid="speaker-picker">
      {!readOnly ? (
        <input type="search" className="speaker-search" placeholder="Search students / staff" value={q}
          onChange={(e) => setQ(e.target.value)} aria-label={`Search people for ${speaker}`} data-testid="speaker-search" />
      ) : null}
      <select value={value || ''} onChange={(e) => onChange && onChange(e.target.value)} aria-label={`Who is ${speaker}?`}
        disabled={readOnly} data-testid="speaker-select">
        <option value="">— who is this? —</option>
        {g.keep ? <option value={g.keep}>{g.keep}</option> : null}
        {g.inClass.length ? (
          <optgroup label={basis ? `In ${basis}` : 'In this class'}>
            {g.inClass.filter((p) => !q.trim() || matches(p, q) || p.label === value).map((p) => (
              <option key={p.id} value={p.label}>{optText(p)}</option>
            ))}
          </optgroup>
        ) : null}
        {g.others.length ? (
          <optgroup label={q.trim() ? `Matching “${q.trim()}”` : 'Everyone else'}>
            {g.others.map((p) => (
              <option key={p.id} value={p.label}>{optText(p)}{p.classroom ? ` (${p.classroom})` : ''}</option>
            ))}
          </optgroup>
        ) : null}
        {g.more > 0 ? <option disabled value="__more">… {g.more} more: type to search</option> : null}
        <optgroup label="Other">
          {g.generic.map((o) => <option key={o} value={o}>{o}</option>)}
        </optgroup>
      </select>
    </div>
  );
}
