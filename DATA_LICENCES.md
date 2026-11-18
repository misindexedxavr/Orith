# Data licences

The code licence (LICENSE) does not cover the data files below. Each keeps its own licence, and the
derived files must be shared under the same terms where the licence requires it.

| Data | Where | Source | Licence |
|---|---|---|---|
| Country facts: codes, capital, continent, TLD, currency, calling code, languages, neighbours, postal format, population, area | `orith/data/facts/country_facts.csv` (rows whose `source` starts with "GeoNames") | GeoNames `countryInfo.txt` | **CC BY 4.0**, credit: GeoNames (geonames.org) |
| Driving side, plug types, voltage, frequency | `country_facts.csv` (rows whose `source` starts with "Wikipedia") | Wikipedia "Left- and right-hand traffic" (revision 1385286602) and "Mains electricity by country" (revision 1380969207), pinned revisions | **CC BY-SA 4.0**, credit: Wikipedia contributors. `country_facts.csv` contains these rows, so the file as a whole is shared alike under CC BY-SA 4.0 |
| Country outlines and Natural Earth continent/subregion rows | `orith/data/facts/countries_ne50m.geojson`, `country_facts.csv` | Natural Earth 1:50m admin 0 countries (repository version 5.2.0-pre) | **Public domain** (naturalearthdata.com) |
| Scripts per country | `country_facts.csv` (rows `field=script`) | Unicode CLDR 49.0.0: `likelySubtags.json`, `territoryInfo.json`, `languageData.json` (cldr-json) | **Unicode License V3**, Copyright Unicode, Inc. The notice ships in `orith/data/facts/LICENSE-CLDR.txt` and must stay with the data |
| Unicode scripts of characters | `orith/data/unicode/` (Scripts.txt, ScriptExtensions.txt, PropertyValueAliases.txt) | Unicode Character Database 18.0.0 | **Unicode License V3**, Copyright Unicode, Inc. |
| Tunisia country pack: admin boundaries and gazetteer | `orith/data/packs/tn/` | OpenStreetMap data via Map Tunisia by Alaa Taieb (https://github.com/Alaa-Taieb/map-tunisia, upstream 1.4.0, OSM data of 2026-10-16) | **ODbL 1.0**: © OpenStreetMap contributors (https://www.openstreetmap.org/copyright); Map Tunisia by Alaa Taieb. A derived database must stay under the ODbL |
| Optional building layer (NOT shipped; you download it) | your buildings folder (`orith doctor` shows it) | GlobalBuildingAtlas GBA.LoD1 tiles from HuggingFace (zhu-xlab), fetched by `tools/fetch_gba.py`; positions decoded from Google Open Buildings Plus Code ids, heights in metres from an ML model (global RMSE about 5.5 m) | **CC BY-NC 4.0** (non-commercial use only). Cite: Zhu et al., ESSD 17, 6647-6668, 2025, doi:10.5194/essd-17-6647-2025. The ODbL part (`--part odbl`) is ODbL 1.0 |
| Online answers (Nominatim, Overpass) cached on your machine | your cache folder (not shipped) | OpenStreetMap | **ODbL 1.0**, © OpenStreetMap contributors |

Exact URLs, retrieval dates, byte sizes and sha256 values of every source file: `orith/data/facts/SOURCES.json`
and `orith/data/unicode/SOURCES.json`. How to rebuild and check them: `docs/data.md`.

Python dependencies keep their own licences: Pillow (MIT-CMU/HPND), openlocationcode (Apache-2.0),
tzdata (Apache-2.0; IANA tz database, public domain), phonenumbers (Apache-2.0, derived from Google's libphonenumber).
