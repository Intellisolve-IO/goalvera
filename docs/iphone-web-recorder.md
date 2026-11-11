# iPhone web recorder — manual test plan

Run this on a real iPhone (current iOS, Safari, and the Home Screen icon), and on one older iOS if one is available. Use the demo teacher login. Read the on-screen sample lesson out loud. Do not record a real student, and do not say a real student's name.

The Hosyond 3.5" board stays the only supported recorder hardware. This plan checks the browser page, then one board recording, to see that the board path still works.

Nothing in the app change deploys the service. The page can record only after `infra/setup_web_recorder.sh` and a deploy that sets `UPLOAD_PUBLIC_URL` (see the README).

## Before you start

1. Sign in as the demo teacher. The Record tab is visible. The page says not to record real students and shows the sample lesson. A real school does not show that reminder: its config sets `SCHOOL_PROFILE` to the school's own id and `WEB_DEMO_RECORDER=off`.
2. Sign in as an RBT, and again as a reviewer. The Record tab is hidden. `POST /v1/web/uploads` and `POST /v1/web/confirm` with that token return 403. An account with no role, or a role other than teacher or admin, gets the same 403.
3. Sign in as an admin. The Record tab is visible, and both endpoints accept the token.
4. Deny the microphone once. The page tells you how to allow it in Settings. Allow it and continue.

## Recording

5. Record about 2 minutes, then Stop. Each piece leaves the phone after it is confirmed (nothing playable remains in the browser's storage). The session shows up under Recordings as pending review, with speakers and times that match what was read.
6. Record 30 minutes with the screen left on. The screen stays awake. The transcript covers the whole half hour.
7. Lock the screen at minute 3 and unlock at minute 4. Tap Resume. It is one session. The review page says the recording paused at about 3:00 for about a minute. Audio from before the lock is still there.
8. Switch to another app for 30 seconds and come back. Same as 7: one session, gap marked.
9. Accept a phone call (or a FaceTime) in the middle of a recording. Recording pauses. After the call, Resume continues the same session and the gap is marked.
10. Turn on airplane mode for 2 minutes while recording. The pieces wait on the phone. When the network is back they upload, and nothing that was recorded is missing. After each piece is confirmed it is deleted from the phone.
11. Close Safari while an upload is still going. Reopen the page. It offers to resume or discard the unsent pieces. Resume finishes the upload.
12. Add to Home Screen, open Goalvera from the icon, and repeat tests 5 and 7.
13. Sign out while a piece is still unsent. The piece is gone from the phone. Signing back in does not offer to resume it.
13a. Start a recording, let one piece confirm, then tap Discard. That session's objects under `web/<session>/` are deleted. Another session's objects are still there.

## Board and storage

14. Record one session on a Hosyond 3.5" board. It still uploads and transcribes as before. The iPhone page did not change that path.
15. In the bucket: public access prevention is enforced. The WAV under `rec/web-…/` is encrypted with the bucket key. The audio pieces under `web/<session>/` are gone after conversion, and a discarded session's pieces are gone immediately (anything abandoned is deleted after one day). The pipeline can read and delete `web/` and can create only `rec/web-*`. Logs have a user reference and a session id, not an email address and not the words that were spoken.
