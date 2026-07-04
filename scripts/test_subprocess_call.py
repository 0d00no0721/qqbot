import json, os
# Simulate the exact same call that model_fallback.py would receive from decision engine
input_data = {
    "context": "test",
    "persona": "test",
    "max_tokens": 500,
    "model": "deepseek-chat",
    "api_base_url": "https://api.deepseek.com/v1/chat/completions",
    "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
}
print(json.dumps(input_data))