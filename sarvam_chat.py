import os

from sarvamai import SarvamAI

api_key = os.environ.get("SARVAM_API_KEY")
if not api_key:
    raise SystemExit("Set SARVAM_API_KEY in .env before running this script.")

client = SarvamAI(api_subscription_key=api_key)
response = client.chat.completions_v2(
    model="glm5.3",
    messages=[{"role": "user", "content": "Reply with a short greeting."}],
    max_tokens=100,
)

print(response.choices[0].message.content)
