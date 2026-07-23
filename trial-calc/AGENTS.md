# calc

A tiny integer expression evaluator: a public `calc.evaluate(expr: str) -> int` plus a
`python -m calc "<expr>"` CLI. `docs/design.md` holds the normative design decisions
(`D-N`) the code must satisfy — read it before changing behaviour.

## Testing — test-first

This project is built test-first with the standard library's `unittest` (no third-party
deps). For any change:

1. Write tests under `tests/` (`tests/test_*.py`) covering the behaviour you are about to add.
2. Implement until your new tests and the whole suite pass:

   ```bash
   python -m unittest discover -s tests -t .
   ```

Keep the suite green at every step.
