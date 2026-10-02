name = "padou"
version = "0.1.0"
model = "Qwen3.5 4B"
print(f"{name} v{version} - {model}")
messages = [
    {"role": "system", "content": "You are padou, a helpful terminal assistant."},
]
while True:
    user_text = input("> ")
    if user_text in ["/exit", "/quit"]:
        print("Goodbye!")
        break
    messages.append({"role": "user", "content": user_text})
    print(f"(padou remembers {len(messages)} messages)")
print(messages)
