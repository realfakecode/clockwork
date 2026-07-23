# <project>

<One or two lines: what this project is and its public entry point. Point at
`docs/design.md` for the normative `D-N` decisions the code must satisfy.>

## Testing — test-first

Build test-first. For any change:

1. Write tests under `tests/` covering the behaviour you are about to add.
2. Implement until your new tests and the whole suite pass:

   ```bash
   <the test command, e.g. python -m unittest discover -s tests -t .>
   ```

Keep the suite green at every step.

<Add any project-wide conventions the workers need: build layout, dependency policy,
naming rules. Keep it short — this file is auto-loaded on every run.>
