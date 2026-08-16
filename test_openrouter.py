import sys
import json
import requests
from pathlib import Path

# Ensure UTF-8 stdout encoding on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_openrouter_key() -> str:
    """Read OpenRouterKey from settings.json if present."""
    settings_file = Path(__file__).resolve().parent / "settings.json"
    if settings_file.exists():
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                key = data.get("settings", {}).get("OpenRouterKey", "").strip()
                if key:
                    return key
        except Exception:
            pass
    return ""


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_openrouter.py \"your prompt here\" [model_name]")
        print("Example: python test_openrouter.py \"Write a short 1-sentence YouTube comment about AI coding\"")
        sys.exit(1)

    prompt = sys.argv[1]
    model_name = sys.argv[2] if len(sys.argv) > 2 else "openai/gpt-oss-20b:free"

    api_key = get_openrouter_key()
    if not api_key:
        api_key = input("Enter OpenRouter API Key (sk-or-v1-...): ").strip()

    if not api_key:
        print("[ERROR] OpenRouter API key is required!")
        sys.exit(1)

    print(f"--> Target Model: {model_name}")
    print(f"--> Sending Prompt: \"{prompt}\"\n")

    # Attempt via OpenAI SDK
    try:
        import openai
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/yt-tool",
                "X-Title": "YouTube Automation Bot Test",
            },
        )
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.7,
        )
        if response and response.choices and response.choices[0].message:
            content = response.choices[0].message.content
            if isinstance(content, str) and content.strip():
                print("=== OpenRouter Response ===")
                print(content.strip())
                sys.exit(0)
    except Exception as exc:
        print(f"[SDK Warning] OpenAI SDK call failed: {exc}. Trying direct REST API...\n")

    # Fallback via direct REST API
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/yt-tool",
        "X-Title": "YouTube Automation Bot Test",
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 150,
        "temperature": 0.7,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        print(f"--> HTTP Status Code: {resp.status_code}")
        data = resp.json()
        if resp.status_code != 200:
            print(f"[API Error Response]:\n{json.dumps(data, indent=2)}")
            sys.exit(1)

        choices = data.get("choices", [])
        if choices and "message" in choices[0]:
            content = choices[0]["message"].get("content")
            if isinstance(content, str) and content.strip():
                print("\n=== OpenRouter Response ===")
                print(content.strip())
            else:
                print(f"[Notice] Model returned empty content choice: {json.dumps(choices[0], indent=2)}")
        else:
            print(f"[Unexpected Response Format]:\n{json.dumps(data, indent=2)}")
    except Exception as exc:
        print(f"[ERROR] Request failed: {exc}")


if __name__ == "__main__":
    main()
