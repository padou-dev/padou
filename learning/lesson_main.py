import httpx

name = "padou"
version = "0.1.0"
model = "Qwen3.5 4B"
SERVER_URL = "http://localhost:8080/v1/chat/completions"

def show_banner():
    print(f"{name} v{version} - {model}")

def make_message(role, content):
    return {"role": role, "content": content}
def ask_model(conversation):
    response = httpx.post(SERVER_URL, json={"messages": conversation}, timeout=120)
    data = response.json()
    return data["choices"][0]["message"]["content"]

show_banner()
messages = [make_message("system", "You are padou, a helpful terminal assistant.")]
while True:
    try:
        user_text = input("> ")
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
        break
    if user_text in ["/exit", "/quit"]:
        print("Goodbye!")
        break
    messages.append(make_message("user", user_text))
    try:
        reply = ask_model(messages)
    except httpx.ConnectError:
        print("Can't reach the model server. Is lamma-server running?")
        message.pop()
        continue
    print(reply)
    messages.append(make_message("assistant", reply))
print(messages)
