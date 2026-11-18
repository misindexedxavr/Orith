# Contributing

- Set up: `python -m venv .venv`, then `pip install -e ".[dev]"`, then `pytest`.
- Rules: no AI models, no OCR engines, no calls to AI services; nothing may open a browser, upload an
  image or run searches on its own; online services are used politely (cache, rate limit, identified
  User-Agent). `orith doctor` and the tests check part of this.
- Every new data row needs a source URL and a retrieval date; rebuild data with the scripts in `tools/`
  (see `docs/data.md`), never by hand.
- The verdict rule in `orith/ledger.py` is deliberately strict. Changes to it need a discussion first.
- Keep pull requests small, with a test for each behaviour change.
