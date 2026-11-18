# Usage guide

`orith` helps a person work out where a photo was taken. You look at the photo; the tool does what plain,
rule-based code can do and keeps your reasoning in a ledger. Results are hints to check, not proof.

## 1. Scan

`orith` does not read images: it turns the clues you see into a ranked, checkable result. `orith IMAGE`
runs every automatic step with no questions and prints `[+]` progress lines (on stderr), then three sections:

- **Verdict**: the best guess (a point, where it comes from and a confidence: HIGH, MEDIUM, LOW or none, with
  the reason), the address of a GPS point (offline: country and installed pack areas; street address with
  `--online`), the most likely country with its own confidence, and whether the strict evidence rule holds.
- **Evidence found**: every clue with its confidence and its source. Consistency problems are shown first as
  `PROBLEM`.
- **Leads to check manually**: what to look for next, map and street-level links for the best point, reverse
  image search pages (upload the image yourself).

`-v` (or `--verbose`) prints the full working instead: verification table, ranked hypotheses, country
reasoning, the file's fingerprint and ELA, the sun, the clues in the case, ledger commands and every link.

Confidence rules (fixed, in `orith/summary.py`): **HIGH** only when the ledger's IDENTIFIED rule holds, or
for a file that declares generative AI; **MEDIUM** for one strong source (GPS with no consistency problem, a
valid phone number, a web domain, the driving side, a plug type, one country matching at least two such
clues); **LOW** for indirect or weak clues (GPS with a problem, a town-level point, writing script, the sun,
building counts, ELA). GPS is never above MEDIUM: metadata can be edited.

Supported files: JPEG, PNG, WebP, TIFF; HEIC/HEIF with the optional extra (`pip install "orith[heic]"`;
without it a HEIC file is accepted, its type reported, and the scan says how to add the decoder). A damaged
or truncated image still has its metadata read, with a warning. A missing, empty, non-image or oversized
file (over 200 MB; `ORITH_MAX_MB` changes the limit) stops with one line. Exit codes: 0 done, 1 internal
error (add `-v` for the traceback), 2 bad input or usage, 130 interrupted.

What the file gives away even without metadata: a date and time in the file name (camera, phone and app
naming), the JPEG encoder fingerprint (quality from the quantization tables; a standard libjpeg table means
the file was saved by software, not straight from most cameras), and perceptual hashes to compare copies.

`--ask` asks short questions first (driving side, text you can read, phone number, web address, currency,
plug type, place names, time and a shadow, anything else); Enter skips, `q` stops. Things the tool has no
data for (vegetation, road markings, bollards, poles, plate formats) are recorded as notes and never used to
narrow countries. `--quiet` hides the progress lines.

**Is the file what it claims to be?** The scan checks the file against itself and prints a Verification
table: GPS clock vs camera clock (and, when the camera stored no offset, the UTC offset they imply), the time
zone in use at the GPS point on that date vs the offset in the file, whether the sun was up at the GPS point
and time, the date in the file name vs the EXIF date, editing software named in the metadata, an embedded
thumbnail that differs, and C2PA / IPTC declarations of generative AI (the C2PA signature is not verified).
A MISMATCH means edited, re-dated, renamed or planted metadata; it never tells where the photo was taken.
For JPEG files it also writes an error level analysis image (`cases/<name>/ela.png`) and lists the areas
whose error stands out (a visual aid; `--no-ela` skips it).

**A whole folder:** `orith FOLDER` checks every image (`--recursive` for subfolders): GPS and country, capture
time, camera, problems, near-duplicates (perceptual hash) and a timeline; `--geojson points.geojson` writes the
GPS positions for QGIS or geojson.io. `orith tools export --case NAME` writes a case's hypotheses, map evidence
and file GPS as GeoJSON.

With the optional building layer (see `docs/data.md`), each hypothesis gets the number of buildings within
500 m, and `--setting city|town|village|open` records a soft clue that supports the hypotheses whose count
fits (rule of thumb printed with the result; a mismatch is shown, never recorded against a hypothesis,
because the layer misses some buildings).

```
orith photo.jpg                          # the same as: orith scan photo.jpg
orith photo.jpg --text "TEXT" --text "MORE TEXT"
orith photo.jpg --shadow-bearing 95        # direction a shadow points to, degrees from north
orith photo.jpg --utc-offset +01:00        # zone of the capture time when the file has none
orith photo.jpg --drives right --plug C    # what you see in the photo
orith photo.jpg --lat 33.92 --lon 8.13     # a point you want checked as a hypothesis
orith photo.jpg --place "Tozeur"           # a place name you read (offline place data; --online for Nominatim)
orith photo.jpg --note "palm trees"        # recorded in the case only
orith photo.jpg -v                         # the full working
orith photo.jpg --online                   # street address of the GPS point, places not in the offline data
orith photo.jpg --json                     # machine-readable
```

The scan reads everything the file says (GPS, capture time and offset, camera and field of view, an
embedded thumbnail that differs, data hidden after the image, location text in XMP/IPTC/PNG/comments),
analyses the text you typed and the text found in the file (scripts, phone numbers, domains, currencies),
narrows the countries with sourced facts, turns places it can resolve offline (GPS position, the area of a
phone number, place names in the metadata) into hypotheses, checks the sun when a time and a measurement
allow it, and prints the ledger's verdict, next steps and map/street-level links.

Everything is recorded in `./cases/<name>/` (`--case NAME` to choose, `--no-record` to only look). A
second scan adds nothing twice. Every clue the scan records is **soft**: it only ranks hypotheses. Making a
clue HARD (it eliminates what it contradicts) is your decision, with `clue add --kind HARD`.

### Reading the full working (`-v`)

- **Evidence found**: what the file and your text say; `soft` = can rank, never decides alone.
- **Most likely countries**: countries ranked by how many clues match, with the reason for each clue
  (`[can exclude]`: a sourced fact of this kind can rule a country out; `[soft]`: only ranks). Countries kept
  only because the table has no data for them are listed on one line. Then what was excluded and by which
  clue, and **what to look for next**: the visible clue (driving side, plug, phone code, web ending,
  currency) that would leave the fewest of these countries, computed from the fact table.
- **Hypotheses (ranked)**: `rel.score` only orders this case's hypotheses (it is not a probability);
  `anchored` = clues tied to recorded map evidence; `agreeing` = those within 250 m.
- **Verdict**: `CANDIDATE` lists what is missing. `IDENTIFIED` needs three independent clues anchored to map
  evidence within 250 m, one of them a fixed-geometry match, no contradiction and three or more hypotheses.

## 2. The ledger

```
orith hyp add --case photo --label "Town B" --lat 33.87 --lon 7.88 --supported-by c1
orith clue add --case photo --text "palm grove, brick facades" --kind SOFT --u 3 --p 4 --r 4 \
    --group scene --supports "h1,h2" --contradicts h3
orith report --case photo
```

Clues are rated uniqueness, permanence and reliability (1 to 5). Keep at least three rival hypotheses.
`tools geocode NAME --case photo` and `tools osm ... --case photo` record map evidence (`e1`, `e2`, ...)
and print the commands that use it (`hyp add --evidence e1`, `clue add --evidence e1`).

## 3. Tools

`orith tools --help` lists them: `triage`, `text`, `facts`, `sun`, `tz`, `geocode`, `where`, `osm`, `test`,
`convert`, `distance`, `destination`, `intersect`, `resection`, `links`, `packs`. Each has `--help`.

## Output

Text by default, with colour only on a terminal. `--no-color` or `NO_COLOR=1` turns colour off; `--ascii`
shows every non-ASCII character as `?` (for consoles that cannot draw non-Latin scripts). `--json` (or
`ORITH_JSON=1`) prints the machine-readable JSON instead; its format does not change with the text view.

## Online lookups

Nominatim and Overpass ask every client to identify itself. Set your contact once:

```
export ORITH_CONTACT="you@example.org"           # Linux / macOS
$env:ORITH_CONTACT = "you@example.org"           # PowerShell
```

or write `{"contact": "you@example.org"}` to `~/.config/orith/config.json`. Without it, online lookups
stop with this explanation; offline commands and cached answers keep working.

The scan goes online only with `--online`, and then only for two things: the street address of a GPS point
(Nominatim reverse geocoding) and place names not found in the offline data. Each request waits at most 15 s
and is tried twice; requests to one host are spaced (Nominatim: 1.1 s) and answers are cached on disk, so a
second run sends nothing. A missing contact, no network, a timeout or an HTTP error never stops the scan: the
step is listed under `skipped:` with the reason. `--no-online` (or `ORITH_NO_ONLINE=1`) overrides
`--online`. No API key is needed or read anywhere. Overpass servers are often
busy or down: when they fail, `tools osm` says that nothing was concluded and prints the query as an
Overpass turbo link plus map links to check by hand. To try a mirror, add
`"overpass_endpoints": ["https://MIRROR/api/interpreter"]` to the same config file and run one query.

## Where things are

`orith doctor` prints the Python, platform, data folders, country packs, the cases and cache folders and
the config file path, and confirms that no AI or OCR module is loaded and no AI service is named in the
code. Environment variables override folders: `ORITH_CASE_DIR`, `ORITH_CACHE_DIR`, `ORITH_CONFIG`,
`ORITH_FACTS`, `ORITH_UNICODE`, `ORITH_PACKS`.

PowerShell: quote comma lists (`"h1,h2"`, `"LAT,LON,R"`); unquoted, PowerShell splits them.
