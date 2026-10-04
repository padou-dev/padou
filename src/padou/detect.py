"""Finding out what this machine has: OS, GPU, RAM, model servers, llama.cpp flags.

Everything here only *looks*; nothing is installed or changed.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# --------------------------------------------------------------------------- #
# Models padou offers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ModelChoice:
    key: str
    label: str
    maker: str
    hf: str  # llama-server -hf <repo>:<quant>
    ollama: str  # ollama pull <tag>
    size_gb: float
    good_for: str
    min_ram_gb: int


MODELS: list[ModelChoice] = [
    # Smallest to biggest. All are Q4_K_M quantised and can call padou's tools.
    ModelChoice("lite", "Qwen3.5 2B", "Alibaba (China)", "bartowski/Qwen_Qwen3.5-2B-GGUF:Q4_K_M", "qwen3.5:2b", 1.4,
                "CPU-only machines and low RAM. Fastest, least capable", 4),
    ModelChoice("granite", "Granite 4.0 Micro", "IBM (USA)", "bartowski/ibm-granite_granite-4.0-micro-GGUF:Q4_K_M", "granite4:micro", 2.1,
                "Small and steady, made for tool calling. Apache 2.0", 6),
    ModelChoice("ministral3b", "Ministral 3 3B", "Mistral AI (France)", "unsloth/Ministral-3-3B-Instruct-2512-GGUF:Q4_K_M", "ministral-3:3b", 2.2,
                "European option, quick on laptops (Ollama 0.13.1+)", 6),
    ModelChoice("phi", "Phi-4-mini", "Microsoft (USA)", "bartowski/microsoft_Phi-4-mini-instruct-GGUF:Q4_K_M", "phi4-mini:3.8b", 2.5,
                "Good at reasoning and maths for its size. MIT licence", 8),
    ModelChoice("standard", "Qwen3.5 4B", "Alibaba (China)", "bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M", "qwen3.5:4b", 3.0,
                "Integrated GPUs and laptops. The default", 8),
    ModelChoice("ministral8b", "Ministral 3 8B", "Mistral AI (France)", "unsloth/Ministral-3-8B-Instruct-2512-GGUF:Q4_K_M", "ministral-3:8b", 5.2,
                "Bigger European option, smarter but slower (Ollama 0.13.1+)", 16),
    ModelChoice("gemma", "Gemma 4 E4B", "Google (USA)", "bartowski/google_gemma-4-E4B-it-GGUF:Q4_K_M", "gemma4:e4b", 5.4,
                "Google's 4B class, strong all-rounder (bigger download)", 8),
    ModelChoice("better", "Qwen3.5 9B", "Alibaba (China)", "bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M", "qwen3.5:9b", 6.2,
                "GPUs with 8 GB+ VRAM. Noticeably smarter", 16),
]
MODELS_BY_KEY = {m.key: m for m in MODELS}


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #


@dataclass
class GPU:
    vendor: str  # "nvidia", "amd", "intel", "apple" or "none"
    name: str = ""
    vram_gb: float | None = None  # dedicated memory; None if unknown
    integrated: bool = False

    @property
    def backend(self) -> str:
        """The llama.cpp build to recommend."""
        if self.vendor == "apple":
            return "metal"
        if self.vendor == "nvidia":
            return "cuda"
        if self.vendor in ("amd", "intel"):
            return "vulkan"
        return "cpu"


@dataclass
class System:
    os_family: str  # "arch", "debian", "fedora", "suse", "macos", "linux", "other"
    os_name: str
    ram_gb: float
    gpu: GPU
    notes: list[str] = field(default_factory=list)


def parse_os_release(text: str) -> tuple[str, str]:
    values = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"')
    ids = (values.get("ID", "") + " " + values.get("ID_LIKE", "")).lower().split()
    name = values.get("PRETTY_NAME") or values.get("NAME") or "Linux"
    for family, members in (
        ("arch", {"arch", "archlinux", "manjaro", "endeavouros", "cachyos", "garuda"}),
        ("debian", {"debian", "ubuntu", "linuxmint", "pop", "elementary", "raspbian"}),
        ("fedora", {"fedora", "rhel", "centos", "rocky", "almalinux", "nobara", "bazzite"}),
        ("suse", {"suse", "opensuse", "opensuse-tumbleweed", "opensuse-leap", "sles"}),
    ):
        if any(i in members for i in ids):
            return family, name
    return "linux", name


def _ram_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    if platform.system() == "Darwin":
        out = _run(["sysctl", "-n", "hw.memsize"])
        if out.strip().isdigit():
            return int(out) / 1024**3
    return 0.0


def _run(argv: list[str], timeout: float = 10) -> str:
    if shutil.which(argv[0]) is None:
        return ""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}


def detect_gpu() -> GPU:
    if platform.system() == "Darwin":
        return GPU("apple", "Apple GPU (Metal)", integrated=True) if platform.machine() == "arm64" else GPU("none")

    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if smi.strip() and "," in smi:
        name, mem = smi.strip().splitlines()[0].rsplit(",", 1)
        try:
            return GPU("nvidia", name.strip(), round(float(mem) / 1024, 1))
        except ValueError:
            return GPU("nvidia", name.strip())

    best: GPU | None = None
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        dev = card / "device"
        try:
            vendor = PCI_VENDORS.get((dev / "vendor").read_text().strip())
        except OSError:
            continue
        if not vendor:
            continue
        vram = None
        try:
            vram = int((dev / "mem_info_vram_total").read_text()) / 1024**3
        except (OSError, ValueError):
            pass
        integrated = vendor == "intel" or (vram is not None and vram < 2.0)
        gpu = GPU(vendor, f"{vendor.upper()} GPU", round(vram, 1) if vram else None, integrated)
        if best is None or (best.integrated and not gpu.integrated):
            best = gpu
    if best and shutil.which("lspci"):
        for line in _run(["lspci"]).splitlines():
            if re.search(r"VGA|3D|Display", line) and best.vendor in line.lower().replace("advanced micro devices", "amd"):
                best.name = line.split(":", 2)[-1].strip()[:70]
                break
    return best or GPU("none")


def detect_system() -> System:
    if platform.system() == "Darwin":
        family, name = "macos", f"macOS {platform.mac_ver()[0]}"
    elif platform.system() == "Linux":
        try:
            family, name = parse_os_release(Path("/etc/os-release").read_text())
        except OSError:
            family, name = "linux", "Linux"
    else:
        family, name = "other", platform.system()
    return System(family, name, round(_ram_gb(), 1), detect_gpu())


def recommend(system: System) -> ModelChoice:
    gpu, ram = system.gpu, system.ram_gb
    if gpu.vram_gb and gpu.vram_gb >= 7.5 and not gpu.integrated:
        return MODELS_BY_KEY["better"]
    if gpu.vendor == "apple" and ram >= 16:
        return MODELS_BY_KEY["better"]
    if (gpu.vendor == "none" and ram < 12) or (ram and ram < 8):
        return MODELS_BY_KEY["lite"]
    return MODELS_BY_KEY["standard"]


# --------------------------------------------------------------------------- #
# Model servers
# --------------------------------------------------------------------------- #


@dataclass
class Backend:
    kind: str  # "llama.cpp", "ollama", "lmstudio"
    url: str
    installed: bool
    running: bool
    models: list[str] = field(default_factory=list)


def _get(url: str) -> httpx.Response | None:
    try:
        return httpx.get(url, timeout=1.5)
    except httpx.HTTPError:
        return None


def detect_backends() -> list[Backend]:
    out = []

    r = _get("http://127.0.0.1:8080/health")
    running = r is not None and r.status_code in (200, 503)
    models = []
    if running:
        m = _get("http://127.0.0.1:8080/v1/models")
        if m is not None and m.status_code == 200:
            models = [x.get("id", "") for x in m.json().get("data", [])]
    out.append(Backend("llama.cpp", "http://127.0.0.1:8080", shutil.which("llama-server") is not None, running, models))

    r = _get("http://127.0.0.1:11434/api/tags")
    models = [x.get("name", "") for x in r.json().get("models", [])] if r is not None and r.status_code == 200 else []
    out.append(Backend("ollama", "http://127.0.0.1:11434", shutil.which("ollama") is not None, r is not None and r.status_code == 200, models))

    r = _get("http://127.0.0.1:1234/v1/models")
    models = [x.get("id", "") for x in r.json().get("data", [])] if r is not None and r.status_code == 200 else []
    lms = shutil.which("lms") is not None or Path.home().joinpath(".lmstudio").exists()
    out.append(Backend("lmstudio", "http://127.0.0.1:1234", lms, r is not None and r.status_code == 200, models))
    return out


# --------------------------------------------------------------------------- #
# llama.cpp: which flags does *this* build understand?
# --------------------------------------------------------------------------- #


@dataclass
class LlamaInfo:
    help_text: str = ""
    devices: str = ""

    def supports(self, flag: str) -> bool:
        return re.search(rf"(^|[\s,]){re.escape(flag)}([\s,=]|$)", self.help_text, re.MULTILINE) is not None

    @property
    def gpu_build(self) -> bool | None:
        """True/False if --list-devices answered, None if we couldn't tell."""
        if not self.devices.strip():
            return None
        return bool(re.search(r"Vulkan\d|CUDA\d|Metal|ROCm\d|SYCL\d", self.devices))


def llama_info(binary: str = "llama-server") -> LlamaInfo:
    return LlamaInfo(_run([binary, "--help"], timeout=15), _run([binary, "--list-devices"], timeout=15))


def build_start_command(model: ModelChoice, info: LlamaInfo, use_gpu: bool, port: int = 8080, context: int = 8192) -> tuple[str, list[str]]:
    """The llama-server command for this model and this build, plus warnings."""
    warnings = []
    argv = ["llama-server", "-hf", model.hf]
    if use_gpu:
        argv += ["-ngl", "99"]
    argv += ["-c", str(context), "--host", "127.0.0.1", "--port", str(port)]
    if not info.help_text:
        # couldn't ask llama-server; assume a current build
        argv += ["--jinja", "--reasoning-budget", "0", "--no-mmproj"]
        return " ".join(argv), warnings
    if info.supports("--jinja"):
        argv.append("--jinja")
    else:
        warnings.append("This llama.cpp build has no --jinja, so tool calling won't work. Update llama.cpp.")
    if info.supports("--reasoning-budget"):
        argv += ["--reasoning-budget", "0"]
    else:
        warnings.append("This build can't switch off 'thinking' (--reasoning-budget); replies may be slow. Update llama.cpp.")
    if info.supports("--no-mmproj"):
        argv.append("--no-mmproj")
    return " ".join(argv), warnings


# --------------------------------------------------------------------------- #
# How to install a server on this system
# --------------------------------------------------------------------------- #


def install_hints(system: System) -> list[tuple[str, str]]:
    """(description, command) pairs for installing llama.cpp or Ollama here."""
    backend, fam = system.gpu.backend, system.os_family
    hints: list[tuple[str, str]] = []
    if fam == "arch":
        if backend == "vulkan":
            hints.append(("llama.cpp, Vulkan build (AUR)", "yay -S llama.cpp-vulkan-git"))
        elif backend == "cuda":
            hints.append(("llama.cpp, CUDA build (AUR): pick the -cuda package from", "yay -Ss llama.cpp"))
        else:
            hints.append(("llama.cpp (AUR): pick a package from", "yay -Ss llama.cpp"))
    if fam == "macos":
        hints.append(("llama.cpp with Metal (Homebrew)", "brew install llama.cpp"))
    elif fam != "arch":
        hints.append(("llama.cpp (Homebrew on Linux)", "brew install llama.cpp"))
        if backend in ("cuda", "vulkan"):
            hints.append((f"llama.cpp with {backend.upper()} (conda-forge)", "conda install -c conda-forge llama.cpp"))
        hints.append(("llama.cpp prebuilt binaries", "https://github.com/ggml-org/llama.cpp/releases"))
    if fam == "macos":
        hints.append(("Ollama (easiest alternative)", "brew install ollama"))
    else:
        hints.append(("Ollama (easiest alternative)", "curl -fsSL https://ollama.com/install.sh | sh"))
    return hints


def on_path(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def is_tty() -> bool:
    try:
        return os.isatty(0) and os.isatty(1)
    except OSError:
        return False
