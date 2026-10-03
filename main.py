name = "padou"
version = "0.1.0"
model = "Qwen3.5 4B"

def show_banner():
    print(f"{name} v{version} - {model}")

def make_message(role, content):
    return {"role": role, "content": content}

show_banner()
messages = [make_message("system", "You are padou, a helpful terminal assistant.")]
while True:
    user_text = input("> ")
    if user_text in ["/exit", "/quit"]:
        print("Goodbye!")
        break
    messages.append(make_message("user", user_text))
    print(f"(padou remembers {len(messages)} messages)")
print(messages)
