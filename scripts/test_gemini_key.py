import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types


def main() -> int:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("FAIL: GEMINI_API_KEY not found in environment/.env")
        return 1

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Reply with exactly: GEMINI_OK",
            config=types.GenerateContentConfig(
                max_output_tokens=20,
                temperature=0.0,
            ),
        )
        text = (response.text or "").strip()
        print(f"SUCCESS: Gemini responded -> {text}")
        return 0
    except Exception as exc:
        print(f"FAIL: Gemini API call failed -> {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
