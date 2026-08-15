"""Router agent: one narrow Gemini call per email, classifying it into a
Category. Nothing else — no extraction, no research. Keep this prompt small
and single-purpose; the extractor owns pulling fields out, and mixing the
two jobs into one prompt degrades both.
"""

from __future__ import annotations

import logging

from core import gemini
from core.config import Settings
from core.models import Category, Email, RouterResult

logger = logging.getLogger(__name__)

# Bodies get truncated before they reach the model: the signal for
# classification is almost always in the first screenful, and the tail is
# usually signatures and legal boilerplate that only cost tokens.
MAX_BODY_CHARS = 3000

SYSTEM_INSTRUCTION = """\
You classify emails for a student's internship application tracker. Assign \
exactly one category.

internship_interview — an interview is being offered, scheduled, confirmed, \
or rescheduled. Includes recruiter calls and any scheduling link sent for a \
conversation with a human.

internship_oa — an online assessment, coding test, take-home task, or \
similar automated evaluation is being assigned. The defining trait is that \
the candidate completes it alone, not with an interviewer.

internship_result — a final outcome for an application: an offer, a \
rejection, or an explicit statement that the candidate is still under \
consideration after a completed stage.

internship_other — genuinely about the student's own internship application \
but none of the above. Application confirmations/acknowledgements, requests \
for documents or availability, and general status updates belong here.

not_relevant — everything else, including job alerts, newsletters, \
marketing, careers-fair advertising, and mass recruiting blasts that are not \
about an application this student actually submitted.

Judge by what the email asks the reader to do, not by keywords it happens to \
contain. A newsletter mentioning the word "interview" is not_relevant.

Automated account and security mail is always not_relevant, even when the \
employer sent it and even when the student is mid-application with them: \
verification codes, one-time passcodes, password resets, login and new-device \
alerts, account-created confirmations, and receipts. Signing in to a careers \
portal is not an application event — nothing has happened to the application \
— so tracking it adds a row with no content and interrupts the reader for \
nothing. A code from ByteDance is a code, not an update on a ByteDance \
application.

A survey or feedback-form confirmation is not_relevant too. Completing a \
questionnaire about a past programme is not a step in an application.

Set confidence between 0 and 1, reflecting genuine uncertainty. Use a value \
below 0.5 when the email is ambiguous or could plausibly fit two categories.\
"""


def _build_prompt(email: Email) -> str:
    body = email.body_text[:MAX_BODY_CHARS]
    if len(email.body_text) > MAX_BODY_CHARS:
        body += "\n[...truncated]"
    return f"From: {email.sender}\nSubject: {email.subject}\n\n{body}"


async def classify(settings: Settings, email: Email) -> RouterResult:
    result = await gemini.generate_json(
        settings,
        system_instruction=SYSTEM_INSTRUCTION,
        prompt=_build_prompt(email),
        response_schema=RouterResult,
        max_output_tokens=256,
    )
    logger.info(
        "Routed %r -> %s (%.2f)", email.subject, result.category.value, result.confidence
    )
    return result
