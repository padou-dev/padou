"""`padou setup`: a first-run wizard that picks a server and a model and writes the config.

It only reads the system and asks questions. The one thing it can *run* for you,
after asking, is starting the model server or `ollama pull`. It never installs
software or uses sudo; it shows you the command instead.
"""

from __future__ import annotations

import subprocess

from rich.text import Text

from padou import compat, config, detect, models, paths, server, ui
from padou.client import Client
from padou.config import ServerConfig
from padou.theme import Theme


class Quit(Exception):
    """The person asked to leave setup (q, Esc or Ctrl+C at a question)."""


_theme = Theme()  # set by run(), for the colours of the questions
QUIT_WORDS = ("q", "quit", "exit")


def _ask(question: str, choices: list[str], default: str, show: str = "") -> str:
    """One answer out of `choices` (Enter = `default`). q, Esc or Ctrl+C raise Quit."""
    prompt = Text.assemble((question + " ", "bold"), (f"[{show or '/'.join(choices)}]", f"bold {_theme.accent}"),
                           (f" ({default})", _theme.accent), ("  q or Esc: quit setup", _theme.dim), (": ", ""))
    while True:
        answer = ui.ask_line(prompt)
        if answer is None:
            raise Quit
        answer = answer.strip().lower() or default
        if answer in QUIT_WORDS:
            raise Quit
        if answer in choices:
            return answer
        ui.console.print(Text(f"Please type one of: {show or ', '.join(choices)}, or q to quit.", style=_theme.warn))


def _yes(question: str, default: bool, auto: bool) -> bool:
    if auto:
        return default
    answer = _ask(question, ["y", "n", "yes", "no"], "y" if default else "n", show="y/n")
    return answer.startswith("y")


def _show_system(theme: Theme, system: detect.System) -> None:
    gpu = system.gpu
    if gpu.vendor == "none":
        gpu_text = "none found (llama.cpp will use the CPU)"
    else:
        kind = "integrated" if gpu.integrated else "dedicated"
        vram = f", {gpu.vram_gb:g} GB VRAM" if gpu.vram_gb and not gpu.integrated else ""
        gpu_text = f"{gpu.name} ({kind}{vram}) → {gpu.backend.upper()} build"
    rows = [["OS", system.os_name]]
    if system.cpu:
        cores = f" ({system.cores} cores)" if system.cores else ""
        rows.append(["CPU", detect.short_cpu(system.cpu) + cores])
    rows += [["RAM", f"{system.ram_gb:g} GB"], ["GPU", gpu_text]]
    ui.table(theme, "Your system", ["", ""], rows, styles=["bold", theme.dim])


def _pick_model(theme: Theme, system: detect.System, auto: bool, installed: list[str] | None = None, ollama: bool = False) -> detect.ModelChoice:
    rec, why = detect.recommendation(system)
    rows = []
    for i, m in enumerate(detect.MODELS, 1):
        mark = Text("← recommended", style=f"bold {theme.accent}") if m is rec else Text("")
        name = m.ollama if ollama else m.label
        have = " (installed)" if installed and any(x.split(":latest")[0] == m.ollama for x in installed) else ""
        rows.append([str(i), name + have, m.maker, f"{m.size_gb:g} GB", m.good_for, mark])
    ui.table(theme, "Pick a model", ["#", "Model", "Made by", "Download", "Good for", ""], rows, styles=[theme.accent, "bold", theme.dim, theme.dim, "", ""])
    ui.info(theme, f"Recommended for this computer: {rec.label} ({why}).")
    if system.ram_gb and rec.min_ram_gb > system.ram_gb:
        ui.info(theme, f"Note: you have {system.ram_gb:g} GB RAM; even the recommended model may be tight.")
    if auto:
        return rec
    default = str(detect.MODELS.index(rec) + 1)
    choice = _ask("Model", [str(i) for i in range(1, len(detect.MODELS) + 1)], default, show=f"1-{len(detect.MODELS)}")
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
            # Already running: with the settings it was started with. A server padou manages
            # has to restart before new settings (context size, cache) take effect.
            procs = server.find(srv) if models.kind(srv) == "llama.cpp" else []
            if not procs:
                return
            if not _yes("The model server is already running with your old settings. Restart it now with the new ones?", True, auto):
                ui.info(theme, "OK. The new settings apply the next time it starts (or: /server restart inside padou).")
                return
            server.stop(procs)
        size = f" (downloads ~{model.size_gb:g} GB the first time)" if model else ""
        if not _yes(f"Start the model server now{size}?", True, auto):
            ui.info(theme, "OK. padou will start it the first time you run `padou`.")
            return
        proc, problem = server.start(srv)
        if proc is None:
            ui.error(theme, problem)
            return
        try:
            label = "starting the model server… (a first download can take a few minutes; Ctrl+C to stop waiting)"
            with ui.ReplyView(theme, label) as view:
                up, detail = server.wait_ready(client, proc, models.ready_timeout(srv),
                                               on_progress=lambda line: view.set_label(f"starting the model server… {line}"))
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


def download_llamacpp(theme: Theme, asset) -> str:
    """Download and unpack a llama.cpp build with a progress line; returns llama-server's path or ""."""
    from padou import llamacpp

    try:
        with ui.ReplyView(theme, f"downloading llama.cpp {asset.tag}…") as view:
            def show(done, total):
                if total:
                    view.set_label(f"downloading llama.cpp {asset.tag}… {100 * done // total}% "
                                   f"({done / 1e6:.0f} of {total / 1e6:.0f} MB)")
            server = llamacpp.install(asset, progress=show)
    except llamacpp.InstallError as exc:
        ui.error(theme, f"Couldn't install llama.cpp: {exc}")
        return ""
    except KeyboardInterrupt:
        ui.info(theme, "Download cancelled; nothing was installed.")
        return ""
    ui.success(theme, f"llama.cpp {asset.tag} is ready ({ui.short_path(server)}).")
    return str(server)


def offer_download(theme: Theme, system: detect.System, auto: bool) -> str:
    """No model server here: offer to download llama.cpp into padou's own folder. Returns llama-server's path or ""."""
    from padou import llamacpp

    pattern, name = llamacpp.variant(system.gpu.vendor)
    ui.console.print(Text("\nNo model server found. padou needs one to run the AI on this computer.", style="bold"))
    if compat.LINUX and system.gpu.vendor not in ("none", "apple") and not llamacpp.has_vulkan():
        ui.console.print(Text("Your graphics chip needs the Vulkan loader for a GPU build (Arch: sudo pacman -S vulkan-icd-loader; "
                              "Ubuntu: sudo apt install libvulkan1). Until then padou uses the CPU build, which is slower.",
                              style=theme.warn))
    try:
        with ui.ReplyView(theme, "asking GitHub for the latest llama.cpp…"):
            asset = llamacpp.find_asset(pattern)
    except llamacpp.InstallError as exc:
        ui.error(theme, f"{exc}.")
        return ""
    size = f"{asset.size / 1e6:.0f} MB" if asset.size else "a small download"
    ui.info(theme, f"padou can download llama.cpp {asset.tag} for {name}: {size}, from github.com/{llamacpp.REPO}.")
    ui.info(theme, f"It goes into padou's own folder ({ui.short_path(llamacpp.home())}): no administrator rights, "
                   "nothing installed system-wide; uninstalling padou can remove it.")
    if system.os_family == "arch":
        ui.info(theme, "(On Arch you can also install it as a package instead: yay -S llama.cpp-vulkan-git)")
    if not _yes("Download llama.cpp now?", True, auto):
        return ""
    return download_llamacpp(theme, asset)


def run(theme: Theme, auto: bool = False) -> bool:
    """Returns True if a config was written. Raises Quit when the person leaves setup at a question."""
    global _theme
    _theme = theme
    ui.console.print(Text.assemble(("❯_ ", f"bold {theme.accent}"), ("padou setup", "bold")))
    ui.info(theme, "Let's find a model server and a model for this machine. Nothing gets installed without you.")
    if not auto:
        ui.info(theme, "You can leave at any question: type q or press Esc.\n")

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

    # 0) nothing at all: offer to download llama.cpp, so there's nothing to install by hand
    if not (llama.installed or llama.running or ollama.installed or ollama.running or lms.running):
        binary = offer_download(theme, system, auto)
        if binary:
            llama.installed, llama.binary = True, binary

    # 1) llama.cpp: padou's default, and the only one it can autostart with a model download
    if llama.installed or llama.running:
        if llama.installed:
            info = detect.llama_info(llama.binary)
            gpu_build = info.gpu_build
        else:
            info, gpu_build = detect.LlamaInfo(), None
        model = _pick_model(theme, system, auto)
        use_gpu = gpu_build if gpu_build is not None else system.gpu.vendor != "none"
        if gpu_build is False and system.gpu.vendor != "none":
            ui.console.print(Text(f"Your llama.cpp is a CPU-only build, but you have a GPU. A {system.gpu.backend.upper()} build would be much faster.", style=theme.warn))
        ctx = detect.context_for(system)
        command, warnings = detect.build_start_command(model, info, use_gpu, context=ctx, binary=llama.binary or "llama-server")
        for w in warnings:
            ui.console.print(Text(w, style=theme.warn))
        srv = ServerConfig(url=llama.url, model=model.hf, model_label=model.label, autostart=True, start_command=command,
                           context_size=ctx)
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
        pick = "1" if auto else _ask("Model", [str(i) for i in range(1, len(names) + 1)], "1", show=f"1-{len(names)}")
        name = names[int(pick) - 1]
        srv = ServerConfig(url=lms.url, model=name, model_label=name, autostart=False, start_command="")
        _write(theme, srv)
        ui.info(theme, "padou can't start LM Studio for you: keep its local server running.")
        return True

    # 4) Nothing yet: show how to install one, and write a config that will work afterwards
    ui.console.print(Text("\nYou can also install a model server yourself with one of these, then run `padou setup` again:", style="bold"))
    rows = [[desc, Text(cmd, style=f"bold {theme.accent}")] for desc, cmd in detect.install_hints(system)]
    ui.table(theme, "How to install a model server here", ["What", "Command"], rows)
    ui.info(theme, "padou doesn't run these for you, because they need your password or download installers.")
    model = _pick_model(theme, system, auto)
    ctx = detect.context_for(system)
    command, _ = detect.build_start_command(model, detect.LlamaInfo(), system.gpu.vendor != "none", context=ctx)
    srv = ServerConfig(model=model.hf, model_label=model.label, autostart=True, start_command=command, context_size=ctx)
    if _yes("Write a config for llama.cpp now, so `padou` works as soon as it's installed?", True, auto):
        _write(theme, srv)
        return True
    return False
