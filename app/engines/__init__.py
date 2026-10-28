"""Speech engines. One is loaded at a time."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Engine(Protocol):
    name: str

    def synthesize(
        self,
        *,
        text: str,
        ref_wav: Path,
        ref_text: str,
        emotion: str,
        out_path: Path,
    ) -> None: ...


class EngineError(RuntimeError):
    pass


def f5_backend() -> str:
    """mlx on this Mac. torch when MLX is not installed, which is the Windows app."""

    import importlib.util

    if importlib.util.find_spec("mlx") and importlib.util.find_spec("f5_tts_mlx"):
        return "mlx"
    return "torch"


def build(name: str) -> Engine:
    if name == "f5":
        if f5_backend() == "mlx":
            from app.engines.f5 import F5Engine

            return F5Engine()
        from app.engines.f5_torch import F5TorchEngine

        return F5TorchEngine()
    if name == "chatterbox":
        from app.engines.chatterbox import ChatterboxEngine

        return ChatterboxEngine()
    if name == "fake":
        from app.engines.fake import FakeEngine

        return FakeEngine()
    raise EngineError(f"Unknown engine '{name}'.")
