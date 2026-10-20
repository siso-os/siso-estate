"""Keep mutable estate records outside the monorepo's immutable live tree."""
from pathlib import Path


def record_path(repo, *parts):
    root = Path(repo)
    if root.name == "core" and root.parent.name == "estate" and (root / "module.json").is_file():
        root = Path.home() / "SISO_Workspace/_data/estate/runtime"
    return str(root.joinpath(*parts))
