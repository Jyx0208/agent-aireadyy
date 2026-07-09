from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> None:
    input_path = Path(os.environ["AGENT_MODEL_ADAPTER_INPUT"])
    output_path = Path(os.environ["AGENT_MODEL_ADAPTER_OUTPUT"])
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    output_path.write_text(
        json.dumps(
            {
                "primary_metric": "sequence_accuracy",
                "sequence_accuracy": 0.81,
                "higher_is_better": True,
                "slices": {"heldout_project": {"sequence_accuracy": 0.73}},
                "adapter_selected_count": payload["summary"]["selected_count"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
