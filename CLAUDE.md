# Working on clockwork

Clockwork is a harness that drives a headless agent through an issue tracker in a
*target* project. Because it operates on other repos, this one is meta — the biggest
hazard is confusing files meant for a target project with clockwork's own code and
docs. Keep the three straight:

- **Clockwork itself** — `orchestrator/` (the `clockwork` CLI), `issues/` (the
  `issues` CLI), `harnesses/` (the agent driver), `README.md`, `docs/`. `clockwork` and
  `issues` are two entry points of this one project.
- **`project-template/`** — scaffolding copied into a target project (design doc,
  naming registry, example `AGENTS.md`)
- **`trials/`** — throwaway fixtures for exercising clockwork. The fixtures under
  `trials/` should read as genuine greenfield projects; the operator guide is
  `trials/README.md`, kept outside the fixture so it can't spoil the run.

Read [README.md](README.md) for the state-machine model and layout, and
[docs/architecture.md](docs/architecture.md) for the loop internals. Nothing here is
strict policy about what the project must be — it's a working system; change it as the
design evolves.

Instructions inside `trials/`, `project-template/`, and the agent prompt strings in
`orchestrator/worker.py` address the clockwork-driven agents- nothing they say is about
the Clockwork repo.

The skills in `skills/` (e.g. `domain-modeling`) are installed into the target project
or user-globally, so they are available to the headless agent, not only to a human
design session. A prompt or a target-repo template can therefore point the agent at a
skill by name and expect it to load the detail on demand — which is why those files
carry a pointer instead of restating the guidance inline.
