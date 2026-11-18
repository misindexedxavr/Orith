# Changelog

## 0.1.0 (unreleased)

First public version.

- File triage (EXIF/GPS/time offsets, thumbnail mismatch, XMP, IPTC, ICC, PNG chunks, trailing data).
- Sun and shadow geometry (NOAA/Meeus), elevation rings; coordinates in every format (UTM, MGRS, Plus
  Codes, geohash); bearings, intersections, resection; IANA time zones.
- Country fact table with a source and date on every row (GeoNames, Wikipedia pinned revisions,
  Natural Earth, Unicode CLDR scripts as a soft clue).
- Text tools: scripts (Unicode 18.0.0), phone numbers, domains, currencies.
- Map search: offline country packs, Nominatim, Overpass (name search over all name keys,
  transliteration variants labelled fuzzy, paging from cache, printed fallbacks when Overpass is down).
- Evidence ledger with a computed verdict (IDENTIFIED only with 3 map-anchored clues, a geometry clue,
  no contradiction and 3+ hypotheses); candidate tests (sun, bearing, distance, time zone).
- `scan` (also `orith IMAGE`): every automatic step on an image, recorded in a case, with a readable report;
  `[+]` progress lines and a verdict-first report; guided questions with `--ask`; "most likely countries"
  with reasons and the next clue to look for, from the fact table.
- File fingerprint: date and time in the file name, JPEG encoder quality from the quantization tables,
  perceptual hashes.
- Optional offline building layer from GlobalBuildingAtlas (`tools/fetch_gba.py`, `tools buildings`, building
  counts per hypothesis, `--setting`), indexed in SQLite.
- Faster: modules load on first use, parsed country packs are cached on disk.
- C2PA content credentials (PNG caBX, JPEG APP11, WebP) and IPTC DigitalSourceType: an AI-GENERATED banner when
  the file declares generative AI (signature not verified).
- Verification checks (GPS clock, time zone at the GPS point, sun, file-name date, editing software,
  thumbnail), error level analysis for JPEG, folder triage with near-duplicates and a timeline, GeoJSON export.
- Human-readable text output with colour (off when not a terminal, with NO_COLOR or --no-color); `--json` keeps
  the machine-readable JSON unchanged. Helpers grouped under `orith tools`; `doctor` and `version`.
- Final reasoning step: one best guess with a confidence level (HIGH / MEDIUM / LOW / none) and its reason, every
  clue with its own confidence and source, leads to check by hand. The default view is Verdict / Evidence found
  / Leads to check manually; `-v` shows the full working.
- Reverse geocoding: country (Natural Earth) and pack areas offline; street address with `--online`.
  `--no-online` overrides it. Online steps wait at most 15 s, are retried once, and are reported as skipped
  (no contact, no network, HTTP error) instead of failing.
- Hardening: one-line errors and exit codes (0 / 1 / 2 / 130) for a missing, empty, non-image, oversized or
  damaged file; no traceback unless `-v`; GPS sanity checks (DMS ranges, latitude/longitude limits, Null
  Island, invalid GPS time); case names cannot leave the cases folder; control and bidi characters in metadata
  never reach the terminal.
- TIFF metadata; HEIC/HEIF with the optional `heic` extra (pillow-heif).
- Synthetic sample files in `tests/samples/` and end-to-end tests on them; ruff in CI.
