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
    user_text = input("> ")
    if user_text in ["/exit", "/quit"]:
        print("Goodbye!")
        break
    messages.append(make_message("user", user_text))
    reply = ask_model(messages)
    print(reply)
    messages.append(make_message("assistant", reply))
print(messages)
