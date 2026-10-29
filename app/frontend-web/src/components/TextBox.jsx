import React, { useEffect, useRef } from 'react';

// A controlled <textarea> / <input> that also accepts text React never hears about
// (tasks#73, tasks#83). Dictation apps such as Wispr Flow, macOS Dictation and Dragon
// sometimes set the box's value directly (through the accessibility API, or as
// `el.value = ...` + an 'input' event). The text shows in the box, but React's own
// onChange doesn't fire, so the state keeps the old text: the next re-render or save
// silently throws the dictated words away. Here native listeners compare the box with
// the last value React rendered and pass any difference on, so dictated or pasted text
// lands and saves exactly like typed text. Typing and normal paste still go through
// onChange as before (a second call with the same text is harmless).
//
//   <TextBox value={text} onValue={setText} rows={4} name="summary" />
//   <TextBox as="input" value={id} onValue={...} />
const EVENTS = ['input', 'change', 'blur', 'keyup', 'compositionend'];

export default function TextBox({ as = 'textarea', value, onValue, ...rest }) {
  const ref = useRef(null);
  const shown = useRef(value);
  const cb = useRef(onValue);
  shown.current = value == null ? '' : String(value);
  cb.current = onValue;
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const sync = () => {
      if (!el.disabled && !el.readOnly && el.value !== shown.current) {
        shown.current = el.value;
        cb.current(el.value);
      }
    };
    const afterPaste = () => setTimeout(sync, 0);
    EVENTS.forEach((e) => el.addEventListener(e, sync));
    el.addEventListener('paste', afterPaste);
    return () => {
      EVENTS.forEach((e) => el.removeEventListener(e, sync));
      el.removeEventListener('paste', afterPaste);
    };
  }, []);
  const Tag = as;
  return <Tag ref={ref} value={value} onChange={(e) => onValue(e.target.value)} {...rest} />;
}
