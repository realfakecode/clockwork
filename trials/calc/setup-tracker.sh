#!/usr/bin/env bash
# Seed the tracker for this project: `tracker init` (its default config ships the full
# workflow) + create the serialized ticket chain. Re-running creates duplicate tickets,
# so run once in a fresh checkout. Requires the `tracker` CLI on PATH.
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

git init .
tracker init

# 1 — lexer (the head of the chain; nothing blocks it)
tracker new lexer "Tokenize arithmetic expressions" --category enhancement \
  --criterion "tokenizes integers and the symbols + - * / ( )" \
  --criterion "tests under tests/ cover the tokenizer and pass" \
  --status ready-for-agent --body - <<'EOF'
Provide a tokenizer the parser can consume. Integers are base-10 (D-2). Scope your tests
to the tokenizer — the parser and evaluator are separate tickets. See docs/design.md
before starting.
EOF

# 2 — parser + evaluator (blocked by the lexer)
tracker new parser "Parse and evaluate with precedence" --category enhancement \
  --blocked-by 1 \
  --criterion "calc.evaluate(str) -> int, respecting D-1 precedence and associativity" \
  --criterion "tests cover the D-1 worked examples through calc.evaluate and pass" \
  --status ready-for-agent --body - <<'EOF'
Implement top-level `calc.evaluate(expr: str) -> int` over the lexer. Precedence and
associativity are normative in D-1; values are ints per D-2. Cover the D-1 worked
examples with tests. Do not add the `/` operator; it is a separate ticket.
EOF

# 3 — CLI (blocked by the evaluator)
tracker new cli "CLI entry point" --category enhancement \
  --blocked-by 2 \
  --status needs-triage --body - <<'EOF'
A thin CLI over calc.evaluate: read the expression from argv, print the result.
EOF

# 4 — division operator (blocked by the evaluator)
tracker new division "Support the / operator" --category enhancement \
  --blocked-by 2 \
  --criterion "the / operator evaluates to an int" \
  --status ready-for-agent --body - <<'EOF'
Add the `/` operator to the core evaluator, per D-2. Make sure the rounding is right.
EOF

echo
echo "Seeded. Frontier:"
tracker ready --unclaimed
