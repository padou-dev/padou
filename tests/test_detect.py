from padou import config, detect
from padou.detect import GPU, LlamaInfo, System


def test_os_release_families():
    assert detect.parse_os_release('ID=arch\nPRETTY_NAME="Arch Linux"')[0] == "arch"
    assert detect.parse_os_release('ID=cachyos\nID_LIKE="arch"')[0] == "arch"
    assert detect.parse_os_release('ID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu 26.04"') == ("debian", "Ubuntu 26.04")
    assert detect.parse_os_release('ID=bazzite\nID_LIKE="fedora"')[0] == "fedora"
    assert detect.parse_os_release('ID="opensuse-tumbleweed"')[0] == "suse"
    assert detect.parse_os_release("ID=gentoo")[0] == "linux"


def sysinfo(vendor="amd", vram=None, integrated=False, ram=32, fam="arch"):
    return System(fam, "x", ram, GPU(vendor, "g", vram, integrated))


def test_recommendations():
    assert detect.recommend(sysinfo("amd", 0.5, True, 32)).key == "standard"  # Z13-style APU
    assert detect.recommend(sysinfo("nvidia", 8, False, 16)).key == "better"  # 8 GB VRAM card
    assert detect.recommend(sysinfo("nvidia", 6, False, 16)).key == "standard"
    assert detect.recommend(sysinfo("none", None, False, 8)).key == "lite"  # CPU only
    assert detect.recommend(sysinfo("intel", None, True, 6)).key == "lite"  # low RAM
    assert detect.recommend(sysinfo("apple", None, True, 16, "macos")).key == "better"


def test_start_command_adapts_to_build():
    model = detect.MODELS_BY_KEY["standard"]
    new = LlamaInfo(help_text="  --jinja  use template\n  --reasoning-budget N\n  --no-mmproj\n")
    cmd, warns = detect.build_start_command(model, new, use_gpu=True)
    assert "-ngl 99" in cmd and "--jinja" in cmd and "--reasoning-budget 0" in cmd and "--no-mmproj" in cmd
    assert warns == []
    old = LlamaInfo(help_text="  -m, --model FNAME\n  -ngl N\n")
    cmd, warns = detect.build_start_command(model, old, use_gpu=False)
    assert "--jinja" not in cmd and "--reasoning-budget" not in cmd and "-ngl" not in cmd
    assert len(warns) == 2


def test_gpu_build_detection():
    assert LlamaInfo(devices="Available devices:\n  Vulkan0: AMD Radeon").gpu_build is True
    assert LlamaInfo(devices="Available devices:\n").gpu_build is False
    assert LlamaInfo().gpu_build is None


def test_install_hints_per_system():
    arch = dict(detect.install_hints(sysinfo("amd")))
    assert any("vulkan" in cmd for cmd in arch.values())
    mac = [cmd for _, cmd in detect.install_hints(sysinfo("apple", fam="macos"))]
    assert "brew install llama.cpp" in mac and "brew install ollama" in mac
    ubuntu = [cmd for _, cmd in detect.install_hints(sysinfo("nvidia", 8, fam="debian"))]
    assert any("conda" in c for c in ubuntu) and any("ollama.com" in c for c in ubuntu)


def test_every_model_renders_a_valid_config():
    import tomllib
    for m in detect.MODELS:
        cmd, _ = detect.build_start_command(m, LlamaInfo(), True)
        text = config.render(config.ServerConfig(model=m.hf, model_label=m.label, start_command=cmd))
        assert tomllib.loads(text)["server"]["start_command"] == cmd


def test_model_catalogue_is_sorted_and_from_several_makers():
    sizes = [m.size_gb for m in detect.MODELS]
    assert sizes == sorted(sizes)
    assert len(detect.MODELS_BY_KEY) == len(detect.MODELS)  # keys are unique
    assert len({m.maker for m in detect.MODELS}) >= 5
    assert {"lite", "standard", "better"} <= set(detect.MODELS_BY_KEY)  # recommend() uses these
    for m in detect.MODELS:
        assert m.hf.endswith(":Q4_K_M") and ":" in m.ollama
