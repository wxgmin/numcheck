# Contributing

Thanks for helping improve NumCheck. The tool is deliberately small and
testable; please keep changes focused and deterministic.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Before submitting

- Run the tests: `python -m pytest -q`
- Regenerate the demo and confirm it still catches all 10 planted errors:
  `python -m numcheck demo --out-dir samples && python -m numcheck review samples/deck.pptx --model samples/model.xlsx --model-old samples/model_old.xlsx --out-dir samples/report --llm off`
- Keep numeric logic deterministic. The LLM may only be used for label
  disambiguation in `numcheck/llm/`.

## Conventions

- Pure-Python, stdlib `argparse`, no runtime shelling.
- Add a unit test for any new parsing/matching behavior.
- Match existing code style (dataclasses, type hints, no comments unless the
  logic is non-obvious).
