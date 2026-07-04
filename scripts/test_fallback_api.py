import httpx, asyncio, json, sys, os

async def test():
    url = "https://api.deepseek.com/v1/chat/completions"
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 20,
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY', '')}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload, headers=headers)
            print(f"status_code: {r.status_code}", file=sys.stderr)
            print(r.text)
    except Exception as e:
        print(f"Exception: {e}", file=sys.stderr)
        print('{"reply":""}')

asyncio.run(test())