"""Ask the API which models this key can actually use.

    python -m scripts.list_models

Model availability shifts, and published tables go stale — older IDs get
retired for new keys while old keys keep working. This is the authoritative
answer for your key. Put the ID you want in .env as GEMINI_MODEL.
"""

from core.config import load_settings
from core import gemini


def main() -> None:
    client = gemini.build_client(load_settings())

    print(f"\nModels supporting generateContent (current default: {gemini.default_model()}):\n")
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        name = model.name.removeprefix("models/")
        display = getattr(model, "display_name", "") or ""
        print(f"  {name:<45} {display}")
    print()


if __name__ == "__main__":
    main()
