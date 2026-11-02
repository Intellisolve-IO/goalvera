# goalvera.app landing page

The static page in `site/` (`index.html`, `styles.css`, `favicon.svg`, `404.html`) is served by Firebase Hosting,
project `goalvera-app`, site `goalvera-app`. No JavaScript, trackers, cookies or forms. Contact is a plain
`mailto:sales@intellisolve.io` link.

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

The first command makes a temporary preview URL; the second publishes to production.

## Custom domain

Add `goalvera.app` (and `www.goalvera.app`) in the Firebase console under Hosting, then add the A/TXT records
Firebase shows to the `goalvera-app` Cloud DNS zone. DNS changes are made by David, not by bots.
