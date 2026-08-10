"""Find a model this key can actually use.

    python -m scripts.list_models            # what the API advertises
    python -m scripts.list_models --probe    # what actually answers a call

Listing is not availability. `models.list` happily returns IDs that return
404 "no longer available to new users" the moment you call them — Google
retires models for new keys while existing keys keep working, and the listing
does not reflect that. Both gemini-2.5-flash and gemini-2.5-flash-lite were
advertised to this project and both 404'd.

So --probe sends one real, minimal request to each candidate and reports what
came back. That is the only trustworthy answer. It costs one request per
model, but each draws on a different model's quota, so it barely dents any
single allowance.

Put a model marked `ok` into .env as GEMINI_MODEL.
"""

from __future__ import annotations

import argparse
import asyncio

from google.genai import errors

from core import gemini
from core.config import load_settings

# Modality keywords that mean "not a text workhorse". Probing an image, audio,
# or robotics model tells us nothing and wastes a request.
NON_TEXT_MARKERS = (
    "tts", "image", "banana", "lyria", "robotics", "computer-use",
    "embedding", "aqa", "customtools",
)

# Probed in this order so the most plausible pipeline models report first and
# you can stop early. Lite models lead: classification is a short single-label
# task, and lite tiers carry far more generous free daily quotas than the
# flagships — the constraint that actually bit this project.
def _rank(name: str) -> tuple[int, str]:
    if "flash-lite" in name:
        return (0, name)
    if "flash" in name:
        return (1, name)
    if "gemma" in name:
        return (2, name)
    return (3, name)


def _is_candidate(name: str) -> bool:
    if any(marker in name for marker in NON_TEXT_MARKERS):
        return False
    # Deep-research and agent models are their own products, not drop-in
    # generateContent workhorses.
    if name.startswith(("deep-research", "antigravity")):
        return False
    return True


def _text_models(client) -> list[tuple[str, str]]:
    out = []
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue
        name = model.name.removeprefix("models/")
        out.append((name, getattr(model, "display_name", "") or ""))
    return out


def list_only(client) -> None:
    print(f"\nModels supporting generateContent (current default: {gemini.default_model()}):\n")
    for name, display in _text_models(client):
        print(f"  {name:<45} {display}")
    print("\nAdvertised, not verified — run with --probe to see which really work.\n")


async def probe(client, limit: int | None) -> None:
    limiter = gemini.get_limiter()
    names = sorted(
        (n for n, _ in _text_models(client) if _is_candidate(n)), key=_rank
    )
    if limit:
        names = names[:limit]

    print(
        f"\nProbing {len(names)} candidate model(s) with one minimal request each, "
        f"paced at {limiter.rpm} req/min.\nLite models first. Ctrl-C once you see "
        "one you want.\n"
    )

    working: list[str] = []
    for name in names:
        await limiter.acquire()
        try:
            # No config at all: this asks "can I call you", not "do you accept
            # our pipeline options". Sending a thinking_config here would
            # conflate an unavailable model with an unsupported argument.
            response = await client.aio.models.generate_content(
                model=name, contents="Reply with the single word: ok"
            )
        except errors.APIError as exc:
            reason = (exc.message or "").split(".")[0][:70]
            print(f"  {exc.code:<5} {name:<38} {reason}")
            continue

        if (response.text or "").strip():
            working.append(name)
            print(f"  ok    {name:<38} answered")
        else:
            # Reasoning tokens can consume the whole default budget. The model
            # is reachable, which is what we are testing.
            working.append(name)
            print(f"  ok    {name:<38} reachable (empty text, likely all thinking)")

    print()
    if working:
        print("Usable models, best candidate first:\n")
        for name in working:
            print(f"  {name}")
        print(f"\nSet one in .env:  GEMINI_MODEL={working[0]}\n")
    else:
        print("No candidate answered. Check GEMINI_API_KEY is valid.\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--probe",
        action="store_true",
        help="send one real request per model to see which are actually callable",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N", help="probe only the first N candidates"
    )
    args = parser.parse_args()

    client = gemini.build_client(load_settings())
    if args.probe:
        asyncio.run(probe(client, args.limit))
    else:
        list_only(client)


if __name__ == "__main__":
    main()
