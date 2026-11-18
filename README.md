# orith

**Where was this photo taken? One command, an honest answer.**

`orith photo.jpg` reads everything the file says about itself, checks whether those facts agree with each
other, narrows the countries with sourced facts from the clues you can see, and combines it all into **one
best guess with a confidence level**, every clue labelled with its own confidence and source, and a list of
leads to check by hand.

It is offline, rule-based and deterministic: **no AI model, no OCR, no AI service, no API key.** It never
opens a browser or uploads your image. A guess is never shown as a fact.

## Demo

A synthetic photo with GPS in its EXIF (`tests/samples/gps.jpg`, drawn by `tools/make_samples.py`):

```
$ orith gps.jpg
[+] Reading the file (EXIF, GPS, XMP, IPTC, C2PA, hidden data) ... GPS, capture time, camera
[-] Looking for a date in the file name ... none
[*] JPEG encoder fingerprint ... quality 88 (standard libjpeg table: saved by software), 4:2:0
[+] Consistency checks (times, zone, sun, name, editing) ... 2 ok, 0 problem(s)
[*] Error level analysis (JPEG) ... 0 area(s) stand out; image gps/ela.png in the cases folder
[+] Where the GPS point is (offline) ... France
[+] Turning places into hypotheses ... EXIF GPS position (can be edited or planted)
[-] Counting buildings near each hypothesis ... no building layer installed (tools/fetch_gba.py)
[*] Sun position at each hypothesis ... computed (measure a shadow to test it)
[*] Ledger ... CANDIDATE, 1 hypothesis(es), 1 clue(s)
orith  gps.jpg  (jpeg, 640x420 px)

VERDICT
-------
  best guess   48.86567, 2.32100  GPS position in the file
  confidence   MEDIUM  GPS from the file, consistent with its clock and time zone; metadata can still be edited
  address      France  (offline: country outline (Natural Earth))
  country      FR France  MEDIUM the country outline containing the GPS position (Natural Earth)
  identified?  no: a lead, not a fact (that needs 3 independent clues agreeing on a map within 250 m; -v shows what is missing)

EVIDENCE FOUND
--------------
  MEDIUM  GPS 48.865667, 2.321000 (FR France)                        EXIF (consistent with the clock and time zone)
  MEDIUM  taken 2024-06-15 14:30:00+02:00 = 2024-06-15 12:30:00 UTC  GPS time in the file
  MEDIUM  camera SampleCam Model 1                                   EXIF

LEADS TO CHECK MANUALLY
-----------------------
  - Google Maps (satellite): https://www.google.com/maps/@?api=1&map_action=map&center=48.865667,2.321000&zoom=18&basemap=satellite
  - OpenStreetMap: https://www.openstreetmap.org/?mlat=48.865667&mlon=2.321000#map=18/48.865667/2.321000
  - Google Earth: https://earth.google.com/web/@48.865667,2.321000,0a,1000d,35y,0h,0t,0r
  - Street View: https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=48.865667,2.321000
  - Google Lens: https://lens.google.com/  (upload the image yourself)
  - Bing Visual Search: https://www.bing.com/visualsearch  (upload the image yourself)
  - Yandex Images: https://yandex.com/images/  (upload the image yourself)
  - TinEye: https://tineye.com/  (upload the image yourself)

  skipped: online lookups (street address, places not in the offline data): off; add --online (needs a contact, see orith doctor)

  saved to the case 'gps'.  Full working (hypotheses, checks, sun, ledger): add -v.  Machine-readable: --json
```

A photo with **no metadata** (most images from social media): you tell `orith` what you can read and see,
and it narrows the countries with sourced facts. It does not invent a point it does not have:

```
$ orith text.png --text "PHARMACIE CENTRALE" --text "Tel: +33 1 42 60 00 00" --text "www.pharmacie-exemple.fr" --drives right
...
VERDICT
-------
  best guess   none
  why          no position: the file has no GPS and no place could be derived from the clues given
  country      FR France  MEDIUM the only country matching all 4 clue(s); 250 excluded by sourced facts
  identified?  no: a lead, not a fact (that needs 3 independent clues agreeing on a map within 250 m; -v shows what is missing)

EVIDENCE FOUND
--------------
  MEDIUM  phone number +33142600000 is valid in FR (Paris)     text you typed; libphonenumber rules
  MEDIUM  web domain ending .fr                                text you typed; fact table
  MEDIUM  cars drive on the right                              you saw it
  LOW     Latin script on signs                                text you typed (a soft clue)
  none    no metadata in the file (stripped or never written)  file

LEADS TO CHECK MANUALLY
-----------------------
  - the phone number belongs to the area 'Paris' (FR): no offline place data for it here; search it on a map, or rerun with --online
  - look for the plug or socket type: it would leave about 1.5 of 2 countries
  ...
```

Bad input never gives a traceback:

```
$ orith missing.jpg
error: file not found: missing.jpg                       (exit code 2)
$ orith not_an_image.jpg
error: not an image orith can read: not_an_image.jpg (no known image signature; JPEG, PNG, WebP, TIFF, HEIC are supported)
```

## Install

Python 3.11 or newer (Linux, macOS, Windows; Kali included).

```
pipx install git+https://github.com/misindexedxavr/orith.git            # recommended: its own environment
pipx install "orith[heic] @ git+https://github.com/misindexedxavr/orith.git"   # with HEIC (iPhone) support
```

From source:

```
git clone https://github.com/misindexedxavr/orith.git
cd orith
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install .                        # or: pip install ".[heic]"   or, to develop: pip install -e ".[dev]"
orith doctor                      # checks the install
```

## Usage

```
orith photo.jpg                                   # verdict, evidence, leads
orith photo.jpg --text "TEXT ON A SIGN" --text "+216 76 452 100"   # text you read: signs, phones, web addresses
orith photo.jpg --drives left --plug G --place "Station Road"      # what you see
orith photo.jpg --ask                             # answer short questions instead of typing options
orith photo.jpg --shadow-bearing 95               # direction a shadow points to: tests the sun at each guess
orith photo.jpg --online                          # street address of the GPS point (OpenStreetMap Nominatim)
orith photo.jpg -v                                # the full working: checks, hypotheses, countries, sun, ledger
orith photo.jpg --json                            # machine-readable, for scripts
orith ~/Pictures/trip --geojson points.geojson    # a whole folder: GPS, times, problems, duplicates, map file
```

Options for every command: `--json`, `-v`/`--verbose`, `--no-online`, `--no-color` (or `NO_COLOR=1`),
`--ascii`. Exit codes: `0` done, `1` internal error (`-v` shows the traceback), `2` bad input or usage,
`130` interrupted. Each scan is saved in `./cases/<name>/` (`--no-record` to only look).

Online lookups are off by default. `--online` needs a contact, because OpenStreetMap's services ask every
client to identify itself: `export ORITH_CONTACT="you@example.org"` (PowerShell:
`$env:ORITH_CONTACT = "you@example.org"`). Without it, or without network, the step is skipped and the
report says why. No API key is used anywhere.

More: `docs/usage.md` (every option, the evidence ledger, the tools), `docs/data.md` (data sources).

## How it works

1. **Read the file.** EXIF, GPS (degrees/minutes/seconds to decimal; impossible values and 0,0 "Null Island"
   are dropped and reported), XMP, IPTC, PNG text, comments, an embedded thumbnail, data hidden after the end
   of the image, C2PA content credentials (including "made by generative AI" declarations), a date in the
   file name, the JPEG encoder fingerprint. JPEG, PNG, WebP, TIFF; HEIC with the `heic` extra.
2. **Check the file against itself.** GPS clock vs camera clock; the time zone in use at the GPS point on that
   date vs the offset in the file; whether the sun was up there and then; file-name date vs EXIF date; editing
   software in the metadata; a thumbnail that differs from the image; error level analysis for JPEG.
3. **Turn what you see into facts.** Phone numbers (libphonenumber), web domains, currencies, writing
   scripts (Unicode), driving side, plug type: each checked against a sourced country table (GeoNames,
   Wikipedia, Unicode CLDR) that can exclude countries. Unknown data never excludes a country.
4. **Place it.** GPS gives a point; the country comes from Natural Earth outlines; offline place data
   (Tunisia ships built in) turns place names and phone areas into points; `--online` adds a street address.
5. **Combine.** Every clue goes into an evidence ledger. The best guess and its confidence follow fixed,
   readable rules (`orith/summary.py`): HIGH only when three independent clues agree on a map within 250 m
   (or the file declares AI); MEDIUM for one strong source; LOW for indirect hints. GPS is never above
   MEDIUM, because metadata can be edited.

## Limitations

- **It does not look at the pixels.** No OCR and no image recognition: you read the signs and type the text
  with `--text`. OCR was left out on purpose. On photos of street signs it is unreliable without a
  large engine (Tesseract and its language data, or a vision model), and a wrong reading would be presented as
  evidence.
- **Metadata can be faked.** That is why GPS is MEDIUM at best and why the consistency checks exist. A
  stripped file (most social-media images) leaves only what you can see.
- **The C2PA signature is not verified**: a declaration is reported as declared.
- **Offline place data ships for Tunisia only.** Elsewhere a place name needs `--online`. The country of a
  GPS point works worldwide.
- The sun cannot tell apart places a few hundred kilometres apart. Writing script is a soft clue only. Time
  zones are never guessed from coordinates.
- Error level analysis is a visual aid, not proof, and means nothing for PNG or AI-generated images.
- Map feature search (`tools osm`, Overpass) is often slow or unavailable; the tool then says nothing was
  concluded.

## Use it responsibly

For your own photos, verification and fact-checking, journalism, research, CTF challenges and public
imagery. **Do not use it to locate, track, identify or harass private individuals, or to find someone's
home.** If a photo shows a private person's home, stop. Respect the laws on privacy where you live.

## Development

```
pip install -e ".[dev]"
python -m pytest            # the test suite, including end-to-end runs on tests/samples/
ruff check .
```

The sample files in `tests/samples/` are synthetic (drawn by `tools/make_samples.py`, no real photo, no
personal data).

## Licence

Code: MIT (`LICENSE`), © 2026 Xavyr. Data: see `DATA_LICENCES.md` (GeoNames CC BY 4.0, Wikipedia-derived rows
CC BY-SA 4.0, Natural Earth public domain, Unicode data under the Unicode License V3, OpenStreetMap data
ODbL).
