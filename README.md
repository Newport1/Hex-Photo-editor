# PhotoHex Lab

A **photo-focused hex editor for JPEG experimentation**.

```text
open JPEG -> inspect segment map -> edit bytes/ranges -> decode preview -> log observations
```

## Features

- PySide6 desktop UI
- JPEG structure tree with segment ranges and risk labels
- Color-coded hex editor:
  - marker/protected bytes
  - APP/metadata segments
  - DQT/DHT/SOF/SOS
  - scan data
  - changed bytes
- Direct byte editing in the hex grid
- Single-byte patch form
- Selected/range operations:
  - XOR selected byte/range
  - ADD selected byte/range
  - SUB selected byte/range
  - SET selected byte/range to arbitrary byte
  - quick SET values: `00`, `7F`, `80`, `FE`
- `skip FF bytes` toggle
- `protect markers/lengths` toggle
- Saved-byte slider panel:
  - save a specific offset
  - adjust that byte from `00` to `FF` with a slider
  - log each slider mutation
- Original vs mutated preview
- Decode status reporting
- Mutation log with notes
- Undo last edit
- Save mutated JPEG
- Save mutation log as JSON
- Auto-generated sample JPEG so it runs without private assets

## Project layout

```text
photohex-lab-pyside6/
  app.py
  photohex/
    jpeg_parser.py
    document.py
  tests/
  tools/
  output/
  samples/
```

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Test

```bash
pytest -q
QT_QPA_PLATFORM=offscreen python app.py --auto-test
python tools/playwright_visual_check.py
```

## Current limitation

This is still a JPEG-focused MVP, not a full generalized binary editor. It intentionally keeps insert/delete disabled so file length does not change, which makes JPEG experiments more survivable.
