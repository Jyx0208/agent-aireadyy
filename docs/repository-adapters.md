# Repository Adapter Architecture

## Purpose

The repository layer isolates upstream database differences from the rest of the pipeline.

## Rule

Do not push MassIVE logic into the PRIDE client. Each supported repository gets its own adapter, and every adapter maps into the same canonical internal model.

## Canonical Model

- `CanonicalProject`
- `CanonicalFile`
- `ProjectContext`
- `FileAsset`

Downstream planning, workflow selection, and execution should only depend on these objects.

## Adapter Interface

Each adapter is responsible for:

- project resolution
- file listing
- file matching
- download URL construction
- metadata mapping
- transfer method selection

## Repository Specific Notes

### PRIDE

PRIDE remains the default first-class source. Existing PRIDE commands continue to work.

### MassIVE

- native accession: `MSV...`
- aliases: `PXD...`
- primary transport: FTP
- metadata often comes from PROXI or dataset cache records

## Index Strategy

MassIVE can be indexed locally to reduce repeated remote lookups. The index stores:

- project accession
- native accession
- PX accession
- file name
- logical path
- transfer method
- raw repository metadata

## Resolution Strategy

1. normalize the user input
2. choose an adapter
3. resolve project candidates
4. list files
5. match the best file candidate
6. create canonical project/file objects
7. pass canonical objects into the planning pipeline

## CLI Commands

```powershell
python -m agent.cli resolve-dataset srm_74_3.raw -r massive
python -m agent.cli resolve-dataset MSV000000001 -r massive
python -m agent.cli resolve-dataset PXD000001 -r pride
```

## Production Guidance

- PRIDE is fully supported today.
- MassIVE is usable when the dataset metadata or dataset cache is available.
- The adapter layer is the correct place for future repository-specific fixes.
