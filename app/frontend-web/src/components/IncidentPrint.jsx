import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { DEFAULT_SCHOOL_NAME } from '../branding.js';
import { PRODUCT_NAME } from '../product.js';

/**
 * One incident's report in a print-ready layout (Download PDF on an incident).
 * The browser makes the PDF: this sets the document title (= the default file name),
 * shows the report and opens the print dialog (Save as PDF). Nothing is stored.
 *
 * `report` comes from the server (POST /api/sessions/<id>/incident_print), which decides
 * what may be printed (first name + last initial; never a school id, full last name or
 * date of birth) and logs the download. Only the fields listed here are ever rendered.
 * A session not approved yet prints as a draft: "DRAFT" watermark on every
 * page, a "Draft, not yet approved by the teacher" line, and DRAFT_ in the title / file name.
 * Approved reports have none of these.
 */
export function IncidentReport({ report }) {
  const r = report;
  const rows = [
    ['Student', r.student],
    ['Class', r.class_code],
    ['Recorded', r.recorded],
    ['Time', r.time_line || r.label.split(' \u00b7 ')[1] || ''],
    ['Teacher', r.teacher_name],
    ['Approved', r.approved_on],
    ['Recording ID', r.recording_id],
  ];
  return (
    <article className={`ip-sheet${r.draft ? ' ip-draft' : ''}`} data-testid="incident-print">
      {r.draft ? <div className="ip-watermark" aria-hidden="true" data-testid="incident-print-watermark">DRAFT</div> : null}
      <table className="ip-frame">
        <thead><tr><td>
          <header className="ip-brand">
            <span className="ip-mark" aria-hidden="true">IS</span>
            <span><strong>{PRODUCT_NAME}</strong> · {r.school_name || DEFAULT_SCHOOL_NAME} · Teacher review</span>
            {r.sample_data ? <span className="ip-sample">Sample data</span> : null}
          </header>
        </td></tr></thead>
        <tbody><tr><td>
          <p className="eyebrow">ABC incident report{r.of > 1 ? ` · ${r.number} of ${r.of}` : ''}</p>
          <h1 className="ip-title" data-testid="incident-print-title">{r.heading || r.label}</h1>
          {r.time_line ? <p className="ip-when" data-testid="incident-print-time">{r.time_line}</p> : null}
          {r.time_note ? <p className="ip-sub" data-testid="incident-print-time-note">{r.time_note}</p> : null}
          <p className="ip-sub">{[r.class_code, r.recorded].filter(Boolean).join(' \u2013 ')}</p>
          {r.draft ? (
            <p className="ip-status" data-testid="incident-print-status">{r.status_line || 'Draft, not yet approved by the teacher'}</p>
          ) : null}
          <dl className="ip-facts">
            {rows.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v || '-'}</dd></div>
            ))}
          </dl>
          {r.fields.map((f) => (
            <section className="ip-field" key={f.label}>
              <h2>{f.label}</h2>
              <p>{f.text || '-'}</p>
            </section>
          ))}
          <p className="ip-note">
            {r.draft ? 'Draft notes' : 'Approved notes'}, downloaded from the review screen. Not sent to anyone.
          </p>
        </td></tr></tbody>
        <tfoot><tr><td><div className="ip-footer-space" /></td></tr></tfoot>
      </table>
      <footer className="ip-footer" data-testid="incident-print-footer">{r.footer}</footer>
    </article>
  );
}

export default function IncidentPrint({ report, onClose, autoPrint = true }) {
  useEffect(() => {
    const before = document.title;
    const html = document.documentElement;
    document.title = report.file_title;          // Chrome / Edge / Safari / Firefox: default PDF file name
    html.classList.add('printing-incident');
    const t = autoPrint ? setTimeout(() => window.print(), 150) : null;
    return () => {
      if (t) clearTimeout(t);
      html.classList.remove('printing-incident');
      document.title = before;
    };
  }, [report, autoPrint]);

  const view = (
    <div className="incident-print-root" role="dialog" aria-modal="true" aria-label={report.title}>
      <div className="ip-bar no-print">
        <span>Print view · use <strong>Save as PDF</strong> in the print dialog</span>
        <span className="ip-bar-actions">
          <button type="button" className="btn primary" onClick={() => window.print()} data-testid="incident-print-again">
            Print / Save as PDF
          </button>
          <button type="button" className="btn ghost-line" onClick={onClose} data-testid="incident-print-close">
            Close
          </button>
        </span>
      </div>
      <IncidentReport report={report} />
    </div>
  );
  return typeof document !== 'undefined' && document.body ? createPortal(view, document.body) : view;
}
