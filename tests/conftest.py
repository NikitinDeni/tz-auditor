"""All test inputs are generated locally and contain only synthetic data."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def synthetic_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("synthetic")
    script = Path(__file__).resolve().parents[1] / "scripts" / "generate_synthetic.py"
    subprocess.run(
        [sys.executable, str(script), "--output-dir", str(output)], check=True, capture_output=True
    )
    return output
