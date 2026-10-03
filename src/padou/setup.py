"""`padou setup`: a first-run wizard that picks a server and a model and writes the config.

It only reads the system and asks questions. The one thing it can *run* for you,
after asking, is starting the model server or `ollama pull`. It never installs
software or uses sudo; it shows you the command instead.
"""

from __future__ import annotations

import subprocess

from rich.prompt import Confirm, Prompt
from rich.text import Text

from padou import config, detect, paths, server, ui
from padou.client import Client
from padou.config import ServerConfig
from padou.theme import Theme


def _yes(question: str, default: bool, auto: bool) -> bool:
    return default if auto else Confirm.ask(question, default=default, console=ui.console)


def _show_system(theme: Theme, system: detect.System) -> None:
    gpu = system.gpu
    if gpu.vendor == "none":
        gpu_text = "none found (llama.cpp will use the CPU)"
    else:
        kind = "integrated" if gpu.integrated else "dedicated"
        vram = f", {gpu.vram_gb:g} GB VRAM" if gpu.vram_gb and not gpu.integrated else ""
        gpu_text = f"{gpu.name} ({kind}{vram}) → {gpu.backend.upper()} build"
    ui.table(theme, "Your system", ["", ""], [["OS", system.os_name], ["RAM", f"{system.ram_gb:g} GB"], ["GPU", gpu_text]], styles=["bold", theme.dim])


def _pick_model(theme: Theme, system: detect.System, auto: bool, installed: list[str] | None = None, ollama: bool = False) -> detect.ModelChoice:
    rec = detect.recommend(system)
    rows = []
    for i, m in enumerate(detect.MODELS, 1):
        mark = Text("← recommended", style=f"bold {theme.accent}") if m is rec else Text("")
        name = m.ollama if ollama else m.label
        have = " (installed)" if installed and any(x.split(":latest")[0] == m.ollama for x in installed) else ""
        rows.append([str(i), name + have, m.maker, f"{m.size_gb:g} GB", m.good_for, mark])
    ui.table(theme, "Pick a model", ["#", "Model", "Made by", "Download", "Good for", ""], rows, styles=[theme.accent, "bold", theme.dim, theme.dim, "", ""])
    if system.ram_gb and rec.min_ram_gb > system.ram_gb:
        ui.info(theme, f"Note: you have {system.ram_gb:g} GB RAM; even the recommended model may be tight.")
    if auto:
        return rec
    default = str(detect.MODELS.index(rec) + 1)
    choice = Prompt.ask("Model", choices=[str(i) for i in range(1, len(detect.MODELS) + 1)], default=default, console=ui.console)
    return detect.MODELS[int(choice) - 1]


def _write(theme: Theme, srv: ServerConfig) -> None:
    existed = paths.CONFIG_FILE.exists()
    path = config.write_example(overwrite=True, text=config.render(srv))
    note = " (your old one is saved as config.toml.bak)" if existed else ""
    ui.success(theme, f"Wrote {ui.short_path(path)}{note}")


def _start_now(theme: Theme, srv: ServerConfig, model: detect.ModelChoice | None, auto: bool) -> None:
    client = Client(srv)
    try:
        if client.health()[0]:
            return
        size = f" (downloads ~{model.size_gb:g} GB the first time)" if model else ""
        if not _yes(f"Start the model server now{size}?", True, auto):
            ui.info(theme, "OK. padou will start it the first time you run `padou`.")
            return
        proc, problem = server.start(srv)
        if proc is None:
            ui.error(theme, problem)
            return
        try:
            with ui.ReplyView(theme, "starting the model server… (a first download can take a few minutes)"):
                up, detail = server.wait_ready(client, proc, srv.startup_timeout)
        except KeyboardInterrupt:
            ui.info(theme, "(stopped waiting; the server keeps loading in the background)")
            return
        if up:
            ui.success(theme, "The model server is up.")
        else:
            ui.error(theme, detail)
            ui.info(theme, server.tail_log())
    finally:
        client.close()


# --------------------------------------------------------------------------- #


def run(theme: Theme, auto: bool = False) -> bool:
    """Returns True if a config was written."""
    ui.console.print(Text.assemble(("❯_ ", f"bold {theme.accent}"), ("padou setup", "bold")))
    ui.info(theme, "Let's find a model server and a model for this machine. Nothing gets installed without you.\n")

    system = detect.detect_system()
    _show_system(theme, system)
    backends = {b.kind: b for b in detect.detect_backends()}
    llama, ollama, lms = backends["llama.cpp"], backends["ollama"], backends["lmstudio"]

    found = [b for b in backends.values() if b.installed or b.running]
    if found:
        ui.table(
            theme,
            "Model servers found",
            ["Server", "Installed", "Running", "Models"],
            [[b.kind, "yes" if b.installed else "–", "yes" if b.running else "–", ", ".join(b.models[:3]) or "–"] for b in found],
            styles=["bold", "", "", theme.dim],
        )

    # 1) llama.cpp: padou's default, and the only one it can autostart with a model download
    if llama.installed or llama.running:
        if llama.installed:
            info = detect.llama_info()
            gpu_build = info.gpu_build
        else:
            info, gpu_build = detect.LlamaInfo(), None
        model = _pick_model(theme, system, auto)
        use_gpu = gpu_build if gpu_build is not None else system.gpu.vendor != "none"
        if gpu_build is False and system.gpu.vendor != "none":
            ui.console.print(Text(f"Your llama.cpp is a CPU-only build, but you have a GPU. A {system.gpu.backend.upper()} build would be much faster.", style=theme.warn))
        command, warnings = detect.build_start_command(model, info, use_gpu)
        for w in warnings:
            ui.console.print(Text(w, style=theme.warn))
        srv = ServerConfig(url=llama.url, model=model.hf, model_label=model.label, autostart=True, start_command=command)
        if llama.running and llama.models and model.hf not in llama.models:
            ui.info(theme, f"Note: llama-server is already running another model ({llama.models[0]}). Restart it to switch: pkill -f llama-server")
        _write(theme, srv)
        _start_now(theme, srv, model, auto)
        return True

    # 2) Ollama
    if ollama.installed or ollama.running:
        model = _pick_model(theme, system, auto, ollama.models, ollama=True)
        srv = ServerConfig(url=ollama.url, model=model.ollama, model_label=model.label, autostart=True, start_command="ollama serve", context_size=8192)
        _write(theme, srv)
        if not ollama.running:
            _start_now(theme, srv, None, auto)
        have = any(x.split(":latest")[0] == model.ollama for x in ollama.models)
        if not have and detect.on_path("ollama") and _yes(f"Download {model.ollama} now with `ollama pull` (~{model.size_gb:g} GB)?", True, auto):
            subprocess.run(["ollama", "pull", model.ollama], check=False)
        elif not have:
            ui.info(theme, f"Download the model with: ollama pull {model.ollama}")
        return True

    # 3) LM Studio (running with its server on)
    if lms.running:
        names = lms.models or ["local-model"]
        rows = [[str(i), n] for i, n in enumerate(names, 1)]
        ui.table(theme, "LM Studio models", ["#", "Model"], rows, styles=[theme.accent, ""])
        pick = "1" if auto else Prompt.ask("Model", choices=[str(i) for i in range(1, len(names) + 1)], default="1", console=ui.console)
        name = names[int(pick) - 1]
        srv = ServerConfig(url=lms.url, model=name, model_label=name, autostart=False, start_command="")
        _write(theme, srv)
        ui.info(theme, "padou can't start LM Studio for you: keep its local server running.")
        return True

    # 4) Nothing yet: show how to install one, and write a config that will work afterwards
    ui.console.print(Text("\nNo model server found. Install one of these, then run `padou setup` again:", style="bold"))
    rows = [[desc, Text(cmd, style=f"bold {theme.accent}")] for desc, cmd in detect.install_hints(system)]
    ui.table(theme, "How to install a model server here", ["What", "Command"], rows)
    ui.info(theme, "padou doesn't run these for you, because they need your password or download installers.")
    model = _pick_model(theme, system, auto)
    command, _ = detect.build_start_command(model, detect.LlamaInfo(), system.gpu.vendor != "none")
    srv = ServerConfig(model=model.hf, model_label=model.label, autostart=True, start_command=command)
    if _yes("Write a config for llama.cpp now, so `padou` works as soon as it's installed?", True, auto):
        _write(theme, srv)
        return True
    return False
