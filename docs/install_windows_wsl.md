# Install padou on Windows (WSL)

padou runs on Linux, and WSL ("Windows Subsystem for Linux") gives Windows a real Linux system, so padou works there just like on a Linux PC. This takes about 15 minutes, most of it downloads.

> [!NOTE]
> You need **Windows 10 (22H2) or Windows 11**. For GPU speed you need an **NVIDIA** graphics card with a current Windows driver. Without one, padou still works on the CPU, just slower.

## 1. Install WSL (once)

Open **PowerShell as Administrator** (right-click Start → *Terminal (Admin)*) and run:

```powershell
wsl --install
```

Restart when asked. After the restart, an **Ubuntu** window opens and asks you to create a Linux username and password. Remember that password, because `sudo` will ask for it.

> [!TIP]
> From now on, open Ubuntu from the Start menu, or use the **Ubuntu** tab in Windows Terminal. All the commands below go in that Ubuntu window, **not** PowerShell.

## 2. Install the tools

In Ubuntu:

```bash
sudo apt update && sudo apt install -y git curl
curl -LsSf https://astral.sh/uv/install.sh | sh        # uv: installs padou
curl -fsSL https://ollama.com/install.sh | sh          # Ollama: runs the AI model
```

Close the Ubuntu window and open a new one, so it picks up the new commands.

**Have an NVIDIA GPU?** Check that WSL can see it:

```bash
nvidia-smi
```

You should see your card listed. Don't install a Linux NVIDIA driver inside WSL: the Windows driver already covers it.

## 3. Install padou

```bash
uv tool install git+https://github.com/padou-dev/padou
```

## 4. Quick setup

```bash
padou
```

On the first run padou:

1. Shows a short disclaimer. Accept it.
2. Starts the **setup wizard**. It finds Ollama and your GPU and recommends a model (with 8 GB+ of VRAM it suggests Qwen3.5 9B). Press **Enter** to accept, or type another number.
3. Offers to download the model with `ollama pull`. Say **yes** (1.4–6.2 GB, depending on the model).
4. Press **Enter** to start chatting.

Check that everything works:

```bash
padou doctor
```

That's it. From now on just open Ubuntu and type `padou`.

## Using padou with your Windows files

Your Windows drives are available inside WSL under `/mnt/c`, `/mnt/d` and so on. padou reads the folder you start it in, so:

```bash
cd /mnt/c/Users/<you>/Documents/some-project
padou
```

Or allow a folder from inside padou: `/folder add /mnt/c/Users/<you>/Documents`.

> [!TIP]
> Files inside WSL (like `~/projects`) are much faster to read than files on `/mnt/c`. For big code projects, keep them in WSL.

## Troubleshooting

| Problem | Fix |
|---|---|
| `wsl --install` says WSL is already installed | Run `wsl --install -d Ubuntu` to add Ubuntu |
| `padou: command not found` | Open a new Ubuntu window. Still missing? Run `source ~/.bashrc`, or `uv tool update-shell` |
| `Can't reach the model server` | Start Ollama: `ollama serve` (leave that window open), or `sudo systemctl start ollama` |
| Replies are slow (a few words per second) | The model is running on the CPU. Run `nvidia-smi`; if it fails, update your NVIDIA driver in Windows and run `wsl --shutdown` in PowerShell, then reopen Ubuntu. Or pick a smaller model with `padou setup` |
| No NVIDIA GPU (AMD/Intel) | GPU support inside WSL is limited for these. Use the CPU-friendly model (Qwen3.5 2B) in `padou setup`, or run padou on a Linux install |
| `/copy` puts odd characters in the clipboard | Windows' `clip.exe` sometimes garbles emoji or accents. Plain text is fine |
| Colours look wrong | Use **Windows Terminal** (the default on Windows 11), not the old console window |
| Want to start over | `rm -rf ~/.config/padou ~/.local/share/padou`, then run `padou` again |

## Uninstall

```bash
uv tool uninstall padou
rm -rf ~/.config/padou ~/.local/share/padou ~/.local/state/padou
```

To remove Ollama's models too: `ollama rm <model>` (list them with `ollama list`).
