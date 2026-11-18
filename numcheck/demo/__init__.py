"""Demo entrypoint: generate the model, previous model, and planted-error deck."""

from __future__ import annotations

from pathlib import Path

from numcheck.demo.build_deck import build_deck
from numcheck.demo.build_model import build_model


def build_demo(out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    model_path = out / "model.xlsx"
    old_model_path = out / "model_old.xlsx"
    deck_path = out / "deck.pptx"

    build_model(model_path, implied_ev=5800.0)
    build_model(old_model_path, implied_ev=5200.0)
    build_deck(deck_path)

    return {
        "model": str(model_path),
        "model_old": str(old_model_path),
        "deck": str(deck_path),
    }
