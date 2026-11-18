# Roadmap: open items for contributors

None of these is built. Each should keep the project's rules: no AI or OCR, nothing opens a browser or
searches on its own, every data row has a source and a retrieval date, the verdict rule is not loosened.

1. **Offline OpenStreetMap extracts.** Query a downloaded `.osm.pbf` (for example a Geofabrik country
   extract; Tunisia is about 85 MB) instead of Overpass, so map search works without a network. Would
   add an optional dependency (pyosmium has wheels for Windows and Linux); it must stay optional.
2. **Resection as one anchored clue.** `resection` computes a camera position from bearings to known
   landmarks; record that result in the ledger as a single geometry clue anchored to the landmarks'
   map evidence, with its residuals, instead of the analyst copying numbers by hand.
3. **Overpass mirror configuration.** `overpass_endpoints` in the user config already lists endpoints
   tried in turn; add a `doctor --network` check that tests each configured mirror once and reports
   which answered (no automatic retries or loops).
4. **More country packs** (admin boundaries + gazetteer, ODbL), in the same format as `data/packs/tn`.
5. **Offline time zone from coordinates** (needs a boundary dataset with a clear licence).
