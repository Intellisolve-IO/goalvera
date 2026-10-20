## What and why

<!-- One or two sentences. Link the card: tasks#NN -->

Card: tasks#

## Checklist

- [ ] Tests pass (backend pytest + frontend npm test run on this PR; e2e scripts run if the UI changed)
- [ ] README / `docs/demo-script.md` / `CHANGELOG.md` updated (or not needed: say why)
- [ ] Firmware version bumped (`firmware/recorder-idf/main/version.h`) if anything under `firmware/` changed
- [ ] Deploys only from `main`: nothing from this branch is deployed until it is merged

## Student data (Ops)

Does this change what student data is collected, where it goes (including the AI), who can see
it, or how long it's kept? If yes, tag Ops.

- [ ] No
- [ ] Yes (Ops tagged, and the change is described above)
