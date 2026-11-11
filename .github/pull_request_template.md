## What and why

<!-- One or two sentences. Link the issue this fixes, if any: Fixes #NN -->

## How it was tested

<!-- Commands you ran and what you checked by hand. -->

## Checklist

- [ ] Tests pass (backend pytest + frontend npm test; e2e scripts if the UI changed)
- [ ] README / `docs/` / `CHANGELOG.md` updated (or not needed: say why)
- [ ] Firmware version bumped (`PROJECT_VER` in `firmware/recorder-idf/CMakeLists.txt`) if anything under `firmware/` changed
- [ ] No real data: no recordings, transcripts, student or staff names, rosters, Wi-Fi names, device IDs or school identifiers (see `CONTRIBUTING.md`)
- [ ] No secrets: keys, tokens, `.pem` files, service-account JSON or `infra/deploy.env`

## Student data

Does this change what student data is collected, where it goes (including the AI), who can see
it, or how long it's kept?

- [ ] No
- [ ] Yes (described above, so maintainers can review the privacy impact)
