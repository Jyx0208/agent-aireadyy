# Cleanup and Handoff Report

Generated on: 2026-06-26

## Cleanup Policy

Only ignored or generated caches, temporary outputs, local sample copies, and duplicate run evidence were removed. Source code, tests, documentation, local `.env` configuration, and the protected benchmark handoff evidence were preserved.

## Removed

```text
.pytest_cache/
.test_tmp/
.agent_cache/
test_aspn_f4_r1_llm_fasta_2/
test_aspn_f4_r1_llm_fasta_4/
data/
runs/benchmark_3_5/
runs/_batches/
runs/Xinyi3_-80__20260611-172915__55420074/
runs/sk_BNWTTS2_C6_160307__20260611-174303__9395af9f/
runs/ai_ready_builds/
runs/baseline_validation/
runs/model_loop/
runs/_protected_benchmark_20260624_4_5_sample_pool/reports/web_smoke/ai_ready_build_advanced_open.png
docs/superpowers/
```

## Preserved

```text
src/
tests/
docs/
profiles/
scripts/
runs/_protected_benchmark_20260624_4_5_sample_pool/
.env
```

## Repository Size After Cleanup

```text
runs: about 1510 MB
src: about 5.6 MB
tests: about 4.7 MB
docs: about 0.1 MB
```

`runs/` currently keeps only the protected benchmark pool.

`docs/` keeps the handoff documents, current technical references, and a small number of legacy/background design notes. The old `docs/superpowers/` planning drafts were removed so they are not mistaken for current execution requirements during handoff.

## Notes

`.env` is a local configuration file. It remains on this machine but is ignored by `.gitignore`. Do not share real API keys in external handoff packages.
