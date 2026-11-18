# Data: where it comes from, how to rebuild it, how to check it

The shipped data lives in `orith/data/`:

- `facts/country_facts.csv` (one row per fact, each with `source`, `source_url` and `retrieved`),
  `facts/countries_ne50m.geojson`, `facts/unmatched.csv` (source rows that matched no country, never guessed),
  `facts/SOURCES.json` (URL, retrieval time, bytes and sha256 of every raw source file), `facts/LICENSE-CLDR.txt`.
- `unicode/Scripts.txt`, `ScriptExtensions.txt`, `PropertyValueAliases.txt` (Unicode 18.0.0) and `unicode/SOURCES.json`.
- `packs/tn/` (Tunisia pack; its own `manifest.json` lists sources, licences and dates).

## Rebuild

The scripts in `tools/` download the raw files into `tools/raw/` (ignored by git) and rebuild the data.
Online sources need your contact (see README, "Online lookups").

```
python tools/fetch_facts_sources.py   # GeoNames countryInfo, Natural Earth 1:50m, two Wikipedia pages (exact revisions recorded)
python tools/fetch_cldr.py            # Unicode CLDR 49.0.0: likelySubtags, territoryInfo, languageData + LICENSE (pinned URLs)
python tools/build_facts.py           # writes orith/data/facts/*
python tools/fetch_unicode.py         # Unicode Character Database 18.0.0 files into orith/data/unicode/
pytest                                # the table is checked by the tests (sources and dates on every row)
```

Wikipedia and GeoNames change over time: a rebuild today gives the current revisions, recorded in
`SOURCES.json`. To reproduce the shipped table exactly, the raw files must have the sha256 values below.

## Optional building layer (GlobalBuildingAtlas)

Not shipped (size and licence). Download only the 5x5-degree tiles that cover your area; the script lists
the sizes first and refuses more than `--max-mb` (default 500) unless you raise it:

```
python tools/fetch_gba.py --bbox 30,5,35,10 --dry-run          # list tiles and sizes
python tools/fetch_gba.py --bbox 30,5,35,10                     # download, index, remove the raw file
python tools/fetch_gba.py --bbox 30.2,7.5,37.6,11.6 --max-mb 1500   # all of Tunisia (4 tiles, about 1.4 GB)
```

Each tile becomes one SQLite index (about 40 bytes per building; Tunisia's south-west tile: 2.1 million
buildings, 83 MB) with its URL, sha256, licence and record counts inside. Buildings whose id holds no
position (Microsoft, OSM, GBA's own ids) are counted and left out: nothing is guessed. Licence: CC BY-NC 4.0
(non-commercial). The data comes from machine learning; every count and height is a hint to check.

## Check the sha256 values

`facts/SOURCES.json` and `unicode/SOURCES.json` record a sha256 for every raw file.

```
python tools/verify_sources.py
```

It checks the shipped Unicode files directly, and the raw fact-table sources in `tools/raw/` after you
ran the fetch scripts (files not downloaded are listed as such). `OK` means byte-identical to the file
the shipped data was built from; `DIFFERENT` usually means the source has a newer revision (exit code 1).
On Linux, `sha256sum tools/raw/facts/* tools/raw/cldr/*` gives the same numbers to compare by eye.
