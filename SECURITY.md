# Security

padou can read files, run a list of approved commands and, when you switch modes, change files (in admin mode also through `sudo`). Its safety rules are a big part of what it is, so problems with them matter.

## Reporting a problem

Please report security problems **privately**, not in a public issue: on GitHub, open the repository's *Security* tab and choose **Report a vulnerability**. Include what you did, what happened, and the padou version (`padou --version`).

You'll get an answer as soon as possible. This is a one-person, spare-time project, so please allow some days. Once it's fixed, you'll be credited in the changelog unless you'd rather not be.

## What counts

Anything that gets around padou's rules, for example:

- reading a secret location (SSH or GPG keys, password stores, browser profiles, `.env` files…), including through symlinks;
- running a command that isn't on the approved list, or passing it arguments the checks should refuse;
- changing a file without the confirmation box, in a mode that shouldn't allow it, or in a place padou never changes (`/proc`, `/boot`, `.git`…);
- the model switching modes, web search or anything else that only you should be able to switch;
- web search reading pages on your computer or local network, or sending anything other than the search words;
- padou seeing or storing your `sudo` password.

## What doesn't

- A model giving a wrong, silly or harmful **answer**: models can be wrong, which is why padou shows every change and needs your yes. Report it as a normal issue if padou could guard against it better.
- A change **you approved** in a confirmation box.
- Problems in the model server (llama.cpp, Ollama, LM Studio) or the model itself: report those to their projects.

## Supported versions

Only the latest release gets fixes. `/update` installs it.
