# goalvera.app landing page

The static page in `site/` (`index.html`, plus `styles.css`, favicons, `img/`, and `404.html`) is served by Firebase Hosting,
project `goalvera-app`, site `goalvera-app`. Contact is a plain `mailto:sales@intellisolve.io` link; there is no
request-info form. The page loads Tailwind CSS, Google Fonts, and Lucide from public CDNs and includes client-side
JavaScript for the interactive demo sections. `firebase.json` Content-Security-Policy allows those CDNs (including
`unsafe-inline` and `unsafe-eval` for the Tailwind CDN compiler).

The multimodal evidence photo is expected at `site/Gemini_Generated_Image_rxb521rxb521rxb5 (1).jpeg` (the homepage
also tries `site/Gemini_Generated_Image_rxb521rxb521rxb5.jpeg`). That file is not in the repo yet.

## Preview locally

```bash
npx firebase-tools emulators:start --only hosting --project goalvera-app
```

Or open `site/index.html` with any static server.

## Deploy

Requires `roles/firebasehosting.admin` on `goalvera-app` for whoever deploys.

```bash
npx firebase-tools hosting:channel:deploy preview --project goalvera-app
npx firebase-tools deploy --only hosting --project goalvera-app
```

The first command makes a temporary preview URL. Only David publishes to production (the second command).

## Custom domain

Add `goalvera.app` (and `www.goalvera.app`) in the Firebase console under Hosting, then add the A/TXT records
Firebase shows to the `goalvera-app` Cloud DNS zone. DNS changes are made by the domain owner.
