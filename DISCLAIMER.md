# Disclaimer

padou is a free, open-source hobby project, provided **"as is", without warranty of any kind**, as stated in the [MIT License](LICENSE). By installing or using padou you accept the following.

## No warranty, no liability

To the maximum extent permitted by applicable law, the author(s) and contributors are **not liable** for any claim, damages, data loss, system damage, security incident or other liability arising from the use of, or inability to use, padou, whether in contract, tort or otherwise. You use padou entirely **at your own risk**.

## AI output can be wrong

padou passes your questions to a language model running on your own machine. Language models, small local ones especially, can and do produce answers that are **incorrect, incomplete, outdated or misleading**, including invented commands, file contents or explanations that sound confident.

- **Always review and understand a command before you run it.** padou suggests commands; *you* decide whether to run them, and you are solely responsible for what you run on your systems.
- Verify important information against official documentation or a qualified person.
- Keep backups of anything you can't afford to lose.

## Not for critical decisions

padou is not designed, tested or suitable for medical, legal, financial, safety-critical, security-critical or other high-stakes decisions. Do not rely on it for them.

## Write mode: you approve, you are responsible

padou is read-only unless **you** turn on write mode with `/write <folder>`. In write mode padou can edit, create, move and delete files and folders in the folder you chose (deleting means moving them to your system's Trash or Recycle Bin), but only after showing you each change and getting your approval. **Every change you approve is your decision and your responsibility.** Read each confirmation box carefully: language models can propose wrong, incomplete or destructive changes, and text inside files you ask padou to read can try to manipulate the model into proposing changes you didn't ask for ("prompt injection"). Backups, the Trash and `/undo` are a convenience, not a guarantee: some drives have no Trash, a Trash can be emptied, and large deletions aren't backed up by padou. Keep your own backups, and use version control such as Git.

## Safeguards, not guarantees

padou's read tools are designed to be read-only: they run no shell, never use `sudo`, validate their arguments, and block known secret locations (SSH keys, password stores, `.env` files and similar). Write mode adds its own limits (approval for every change, typing a folder's name to delete it, the Trash instead of permanent deletion, backups, no system or hidden folders). These safeguards reduce risk; **they are not a guarantee**. Software has bugs, and a tool that reads files can still show their contents to the model and on your screen. Only run padou on systems and data you are entitled to access, and don't point it at folders containing information you aren't comfortable having read.

## Third-party software and models

padou does not include any AI model. Models (for example Qwen by Alibaba, Gemma by Google, Phi by Microsoft, Granite by IBM or Ministral by Mistral AI) and model servers (llama.cpp, Ollama, LM Studio) are **third-party** software, downloaded by those tools from their own sources and governed by **their own licences and terms**. Check the licence on each model's page before use, especially for commercial use. The author of padou is not responsible for third-party software, models or the content they produce.

## No affiliation

padou is an independent project. It is not affiliated with, endorsed by or sponsored by Alibaba (Qwen), Google (Gemma), Microsoft (Phi), IBM (Granite), Mistral AI (Ministral), the llama.cpp project, Ollama, LM Studio, Omarchy, Anthropic or any other company or project named in its documentation. All trademarks belong to their respective owners.

## Privacy

padou has no telemetry. It sends your conversations only to the model server you configure (by default, on your own machine). At most once a day it asks GitHub's public API for the latest padou release, which sends nothing about you or your conversations; turn it off with `[updates] check = false`. Conversations are saved locally on your computer (see "Where padou keeps things" in the README). You are responsible for the data you choose to share with the model and for protecting your own machine.

---

*This disclaimer is not legal advice. If you need legal certainty for your situation, consult a lawyer.*
