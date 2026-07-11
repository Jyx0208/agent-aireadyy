# Domain Docs

This repository uses a single-context domain documentation layout.

## Before Exploring

Read these sources when they exist and are relevant:

- `CONTEXT.md` at the repository root for the domain glossary and system context.
- ADRs under `docs/adr/` for architectural decisions affecting the area being changed.

If these files do not exist, proceed silently. Domain-modeling work may create them later when terminology or decisions need to be recorded.

## Layout

```text
/
|-- CONTEXT.md
|-- docs/adr/
`-- src/
```

Use vocabulary defined in `CONTEXT.md` in issues, tests, code, and design documents. If proposed work conflicts with an existing ADR, identify that conflict explicitly instead of silently overriding the decision.
