import React from 'react';
import { DEFAULT_BRANDING } from '../branding.js';

/** Top bar brand: the school logo (or its badge) and name. Fixed box sizes so the bar never moves;
 *  long names end in an ellipsis (full name in the tooltip). */
export default function SchoolBrand({ branding, logoUrl = null }) {
  const name = (branding && branding.school_name) || DEFAULT_BRANDING.school_name;
  const badge = (branding && branding.badge_text) || DEFAULT_BRANDING.badge_text;
  return (
    <div className="school-brand" data-testid="school-brand">
      <span className="school-mark" aria-hidden={logoUrl ? undefined : 'true'}>
        {logoUrl
          ? <img src={logoUrl} alt={`${name} logo`} data-testid="school-logo" />
          : <span className="school-badge" data-testid="school-badge">{badge}</span>}
      </span>
      <span className="school-name" title={name} data-testid="school-name">{name}</span>
      <span className="school-app">Teacher review</span>
    </div>
  );
}
