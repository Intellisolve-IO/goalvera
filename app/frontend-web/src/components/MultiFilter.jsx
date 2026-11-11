import React, { useEffect, useId, useRef, useState } from 'react';

/**
 * Checkbox dropdown. An empty `selected` list means All.
 * Keyboard: Enter/Space toggles the menu, ArrowDown enters it, arrows move
 * between options, Space toggles a checkbox, Escape closes.
 */
export default function MultiFilter({ label, options, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const panelId = useId();
  const all = selected.length === 0;
  const summary = all ? 'All' : selected.length === 1 ? selected[0] : `${selected.length} selected`;

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
        rootRef.current?.querySelector('button.filter-btn')?.focus();
      }
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  function toggle(opt) {
    if (selected.includes(opt)) onChange(selected.filter((x) => x !== opt));
    else onChange([...selected, opt]);
  }

  function onButtonKey(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      requestAnimationFrame(() => rootRef.current?.querySelector('.filter-panel input')?.focus());
    }
  }

  function onPanelKey(e) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return;
    const inputs = [...rootRef.current.querySelectorAll('.filter-panel input')];
    if (!inputs.length) return;
    const i = inputs.indexOf(document.activeElement);
    e.preventDefault();
    if (e.key === 'Home') inputs[0].focus();
    else if (e.key === 'End') inputs[inputs.length - 1].focus();
    else if (e.key === 'ArrowDown') inputs[(i + 1 + inputs.length) % inputs.length].focus();
    else inputs[(i - 1 + inputs.length) % inputs.length].focus();
  }

  return (
    <div className="multi-filter" ref={rootRef}>
      <button
        type="button"
        className="filter-btn"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onButtonKey}
      >
        <span className="filter-label">{label}</span>
        <span className="filter-value">{summary}</span>
      </button>
      {open && (
        <div
          id={panelId}
          role="group"
          aria-label={`${label} filter`}
          className="filter-panel"
          onKeyDown={onPanelKey}
        >
          <label>
            <input type="checkbox" checked={all} onChange={() => onChange([])} />
            All
          </label>
          {options.map((opt) => (
            <label key={opt}>
              <input type="checkbox" checked={selected.includes(opt)} onChange={() => toggle(opt)} />
              {opt}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
