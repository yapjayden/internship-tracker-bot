"""Find which request option the current model rejects.

A 400 from Gemini is bare — "Request contains an invalid argument", no field
name — so the only way to identify the culprit is to add options one at a time
and see which one flips a working call into a failing one.

    python -m scripts.diagnose_gemini

Runs a handful of calls, paced by the shared rate limiter, and prints the
first configuration that fails. Use it whenever the model changes underneath
you and everything starts 400ing.
"""

from __future__ import annotations

import asyncio

from google.genai import errors, types
from pydantic import BaseModel

from core import gemini
from core.config import load_settings


class _Probe(BaseModel):
    """Deliberately mirrors RouterResult's shape: a str-enum-ish field and a
    float. If structured output is the problem, it shows up here."""

    label: str
    score: float


PROMPT = "Classify this as 'a' or 'b' and give a score between 0 and 1: hello"


def _cases() -> list[tuple[str, types.GenerateContentConfig]]:
    schema_opts = dict(
        response_mime_type="application/json",
        response_schema=_Probe,
    )
    return [
        ("bare prompt, no config", types.GenerateContentConfig()),
        (
            "+ system_instruction",
            types.GenerateContentConfig(system_instruction="You are a classifier."),
        ),
        (
            "+ max_output_tokens=256",
            types.GenerateContentConfig(
                system_instruction="You are a classifier.", max_output_tokens=256
            ),
        ),
        (
            "+ response_schema (structured JSON)",
            types.GenerateContentConfig(
                system_instruction="You are a classifier.",
                max_output_tokens=2048,
                **schema_opts,
            ),
        ),
        (
            "+ thinking_budget=0        (2.5 dialect)",
            types.GenerateContentConfig(
                system_instruction="You are a classifier.",
                max_output_tokens=2048,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                **schema_opts,
            ),
        ),
        (
            "+ thinking_level=MINIMAL   (3.x dialect)",
            types.GenerateContentConfig(
                system_instruction="You are a classifier.",
                max_output_tokens=2048,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.MINIMAL
                ),
                **schema_opts,
            ),
        ),
    ]


async def main() -> None:
    settings = load_settings()
    client = gemini.build_client(settings)
    limiter = gemini.get_limiter()
    model = gemini.default_model()

    cases = _cases()
    print(
        f"\nProbing {model!r} with {len(cases)} configurations, "
        f"paced at {limiter.rpm} req/min.\n"
    )

    for label, config in cases:
        await limiter.acquire()
        try:
            response = await client.aio.models.generate_content(
                model=model, contents=PROMPT, config=config
            )
        except errors.APIError as exc:
            print(f"  FAIL  {label}")
            print(f"        {exc.code} {exc.message}")
            continue

        # A call can succeed at the HTTP level and still come back empty when
        # reasoning tokens have eaten the whole output budget — worth telling
        # apart from a clean pass.
        text = (response.text or "").strip()
        finish = response.candidates[0].finish_reason if response.candidates else None
        if text:
            print(f"  ok    {label}")
        else:
            print(f"  EMPTY {label}  (finish_reason={finish})")

    print(
        "\nThe first FAIL is the option this model rejects. Set GEMINI_MODEL in "
        ".env to something that accepts what the pipeline needs, or adjust\n"
        "core/gemini.py THINKING_VARIANTS to prefer the dialect that passed.\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
