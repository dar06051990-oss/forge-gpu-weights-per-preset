# Changelog

## 1.0.1 — 2026-11-15

- Fixed jerky slider movement and number entry being overwritten by delayed Gradio updates.
- Removed active slider feedback from the input handler.
- Debounced settings writes (0.45 seconds) and save immediately on slider release.
- Flush pending changes before switching presets; preserve per-preset values.
- Tested in Forge Neo by the project owner before publication.


## 1.0.0 — 2026-09-23

- Initial public release.
- Added GPU Weights (MB) slider to the Forge Neo top model controls.
- Added per-preset GPU weight storage.
- Added automatic value application when switching UI presets.
- Added Settings entries for every available preset.
- No Forge core-file modifications required.
