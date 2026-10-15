# kora documentation

This folder is written for people. It holds what kora is for, what it must do, how it is built and why. The long reference material that an engineer or a coding agent consults section by section (normative specifications, detailed diagrams, research, analyses, review records) lives in [`knowledge/`](../knowledge/README.md) and is linked from here wherever a decision depends on it.

## Reading order

1. [positioning.md](positioning.md): the one-liner, the problem, who kora is for, the primary contrast (Archon), the canonical use case, non-goals, evidence and hypotheses.
2. [requirements/functional.md](requirements/functional.md) and [requirements/non-functional.md](requirements/non-functional.md): what the product must do and under which constraints; every requirement has a stable identifier (`FR-n.m`, `NFR-n.m`) and an MVP or post-MVP tag.
3. [architecture.md](architecture.md): the two-page map of the system: context, containers, stack and the boundaries that must not blur.
4. [decisions/](decisions/README.md): the architecture decision records, one per significant decision, with context, alternatives and consequences.
5. [proposals/](proposals/README.md): changes that are designed but deferred, in a fixed template with a status.

## Conventions

- Everything in the repository is in English and must be understandable without the conversation that produced it; identifiers that resolve inside the repository (`FR-6.3`, `NFR-5.5`, `ADR-0007`, `DD-12`) are fine, labels that exist only in a session are not.
- Decisions are made by the project owner and recorded here; the specifications in `knowledge/specs/` apply them and never contradict them. When the two disagree, the decision record wins and the specification is fixed.
- Research and analyses are evidence, not decisions. They are cited from a decision record or from positioning, never the other way round.
