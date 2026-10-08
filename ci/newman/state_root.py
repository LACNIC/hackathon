"""Keep module artifacts and ignored project state in their trusted Git roots."""

from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_state_root(module_root: Path, candidate: Path | None) -> Path:
    """An explicit state root must be the canonical Git root containing the module."""
    module = module_root.resolve()
    if candidate is None:
        return module
    lexical = candidate.absolute()
    state = lexical.resolve()
    if lexical != state or not state.is_dir():
        raise ValueError("State root must be an existing regular path without symlinks")
    if state != module and state not in module.parents:
        raise ValueError("State root must contain the WS module")
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=module,
                            capture_output=True, text=True, check=False)
    if result.returncode or Path(result.stdout.strip()).resolve() != state:
        raise ValueError("State root must be the module's trusted Git root")
    return state
