"""Copy the packaged Project Keystone originals to a destination."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile

ASSET_DIR = Path(__file__).with_name("assets")
DEMO_FILES = {
    "deck": ("Project_Keystone_Board_Materials.pptx", "Original PowerPoint"),
    "model": ("Keystone_Model_v4.xlsx", "Current Excel model"),
    "model_old": ("Keystone_Model_v3_prior.xlsx", "Prior Excel model"),
}


def build_demo(out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, (name, _label) in DEMO_FILES.items():
        destination = out / name
        copyfile(ASSET_DIR / name, destination)
        paths[key] = str(destination)
    return paths
