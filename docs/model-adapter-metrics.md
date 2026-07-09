# Model Adapter Metrics

`run-dataset-model-loop` can consume lightweight model evaluation outputs without
running a large training job. This is the bridge from model results back into the
Data Scientist Agent loop:

```text
dataset recipe
-> model metrics JSON / CSV / TSV / log
-> normalized model_eval_summary.json
-> model_failure_modes.json
-> model_informed_expansion_plan.json
```

## Supported Inputs

Use `--metrics-file` for an already-produced evaluation file:

```powershell
python -m agent.cli run-dataset-model-loop `
  --recipe-dir runs/.../recipe `
  --task-type denovo `
  --adapter xuanjinovo_template `
  --metrics-file path/to/xuanjinovo_eval_metrics.tsv `
  --output-dir runs/model_loop/example
```

Supported formats:

- JSON
- CSV
- TSV
- Plain text log with `metric: value` or `metric = value` lines

Supported adapter templates:

- `dry_run`
- `xuanjinovo_template`
- `massnet_eval`
- `casanovo_eval`

The adapter template only controls metric normalization/provenance for
`--metrics-file`; it does not run training by itself.

## Metric Examples

Long table TSV/CSV:

```text
metric              value   split
sequence_accuracy   0.66    overall
peptide_recall      0.61    overall
sequence_accuracy   0.49    heldout_project
sequence_accuracy   0.42    phosphotyrosine
sequence_accuracy   0.51    high_charge
```

Plain log:

```text
cosine similarity: 83.4%
pearson = 0.76
loss: 0.31
```

JSON:

```json
{
  "primary_metric": "sequence_accuracy",
  "sequence_accuracy": 0.62,
  "train": {"sequence_accuracy": 0.91},
  "heldout_project": {"sequence_accuracy": 0.58},
  "slices": {
    "phosphotyrosine": {"sequence_accuracy": 0.31},
    "high_charge": {"sequence_accuracy": 0.43}
  }
}
```

Common aliases are normalized, including:

- `seq_acc`, `peptide_acc`, `exact_match` -> `sequence_accuracy`
- `peptide recall`, `pep_recall` -> `peptide_recall`
- `aa_precision` -> `amino_acid_precision`
- `cosine similarity`, `spectral_angle` -> `cosine_similarity`
- `auc`, `auroc`, `roc_auc` -> `auc`

## External Command Mode

Use `--adapter-command` only when the agent should call an external training or
evaluation command. The command receives:

- `AGENT_MODEL_ADAPTER_INPUT`
- `AGENT_MODEL_ADAPTER_OUTPUT`
- `AGENT_MODEL_ADAPTER_CONTRACT`
- `AGENT_MODEL_ADAPTER_TASK_TYPE`
- `AGENT_MODEL_ADAPTER_RECIPE_DIR`

The command must write JSON metrics to `AGENT_MODEL_ADAPTER_OUTPUT`.

```powershell
python -m agent.cli run-dataset-model-loop `
  --recipe-dir runs/.../recipe `
  --task-type denovo `
  --adapter xuanjinovo_template `
  --adapter-command "python run_xuanjinovo_eval.py" `
  --output-dir runs/model_loop/external_eval
```

## Output

The model loop writes:

- `model_adapter_contract.json/md`
- `model_adapter_input_manifest.json/csv`
- `model_eval_summary.json`
- `model_failure_modes.json`
- `model_informed_gap_report.json/md`
- `model_informed_expansion_plan.json`

The agent then uses these metrics to recommend gap-filling actions such as
adding phosphotyrosine examples, high-charge peptides, independent projects,
new instruments, or higher-yield data.
