name = "padou"
version = "0.1.0"
model = "Qwen3.5 4B"
print(f"{name} v{version} - {model}")
messages = [
    {"role": "system", "content": "You are padou, a helpful terminal assistant."},
    {"role": "user", "content": "Hello padou"},
]
print(len(messages))
print(messages[1]["content"])
messages.append({"role": "assistant", "content": "Hi! How can I help?"})
print(len(messages))
print(messages[2]["role"])
print(messages[0]["content"])
