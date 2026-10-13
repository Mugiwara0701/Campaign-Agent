"""What was measured and on what: the model (with its digest), the machine, the code. Best effort, never fatal."""

import os
import platform
import shutil
import subprocess  # noqa: S404
from typing import Any

import httpx

from campaign_agent.settings import Settings


def _git_sha() -> str | None:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _ram_gb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            kb = int(f.readline().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return round(kb / 1024 / 1024, 1)


def _gpu() -> str | None:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip().replace("\n", "; ") or None


def machine() -> dict[str, Any]:
    return {
        "cpu": platform.processor() or platform.machine(),
        "cores": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "gpu": _gpu(),
        "os": platform.platform(),
    }


def model_info(settings: Settings) -> dict[str, Any]:
    """The model as the backend reports it. For Ollama this includes the digest that pins the weights."""
    info: dict[str, Any] = {"backend": settings.llm_backend, "model": settings.llm_model}
    if settings.llm_backend != "ollama":
        return info
    try:
        tags = httpx.get(settings.llm_base_url.rstrip("/") + "/api/tags", timeout=10).json()
    except (httpx.HTTPError, ValueError):
        return info
    for m in tags.get("models", []):
        if settings.llm_model in (m.get("name"), m.get("model")):
            details = m.get("details", {})
            info.update(
                digest=m.get("digest"),
                quantization=details.get("quantization_level"),
                parameter_size=details.get("parameter_size"),
                family=details.get("family"),
                size_bytes=m.get("size"),
            )
    return info


def loaded_memory(settings: Settings) -> dict[str, Any] | None:
    """What Ollama holds in memory right now (call it after a run, while the model is still loaded)."""
    if settings.llm_backend != "ollama":
        return None
    try:
        ps = httpx.get(settings.llm_base_url.rstrip("/") + "/api/ps", timeout=10).json()
    except (httpx.HTTPError, ValueError):
        return None
    for m in ps.get("models", []):
        if settings.llm_model in (m.get("name"), m.get("model")):
            return {"size_bytes": m.get("size"), "size_vram_bytes": m.get("size_vram")}
    return None


def code_version() -> str | None:
    return _git_sha()
