"""Write the API's OpenAPI document, which the dashboard's types are made from.

uv run -m backend.openapi dashboard/openapi.json
"""

import json
import sys
from pathlib import Path

from backend.main import app

if __name__ == "__main__":
    Path(sys.argv[1]).write_text(json.dumps(app.openapi(), indent=2, sort_keys=True))
