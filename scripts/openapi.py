"""Print the API's OpenAPI document, ignoring .env (the web app's types are generated from it: `npm run api:types`).

uv run python scripts/openapi.py > web/openapi.json
"""

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app.config import hermetic_settings
from app.main import create_app

if __name__ == "__main__":
    print(json.dumps(create_app(hermetic_settings()).openapi()))
