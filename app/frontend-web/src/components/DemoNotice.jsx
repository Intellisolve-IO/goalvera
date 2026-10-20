import React from 'react';
import { DEMO_NOTICE } from '../opsText.js';

/** Shown at the top of every page: sign-in, sign-up, verify, waiting, review. */
export default function DemoNotice() {
  return (
    <div className="demo-notice" role="note" data-testid="demo-notice">
      {DEMO_NOTICE}
    </div>
  );
}
