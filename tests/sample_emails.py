"""Synthetic emails for testing the router and extractor.

Deliberately fabricated rather than copied from a real inbox, so test runs
never depend on — or persist — actual mail. Several are near-misses chosen
to catch keyword-matching: a newsletter containing "interview", a rejection
worded warmly, a careers-fair blast that looks like an invitation.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from core.models import ApplicationStatus, Category, Email, MailSource


def _email(message_id: str, sender: str, subject: str, body: str) -> Email:
    return Email(
        source=MailSource.GMAIL,
        message_id=message_id,
        sender=sender,
        subject=subject,
        received_at=datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
        body_text=body,
    )


# (email, expected_category)
SAMPLES: list[tuple[Email, Category]] = [
    (
        _email(
            "s1",
            "university-recruiting@shopee.com",
            "Interview Invitation — Software Engineer Intern (Summer 2027)",
            "Hi Jayden,\n\nThank you for applying to the Software Engineer Intern "
            "programme. We'd like to invite you to a 45-minute technical interview "
            "with two of our engineers.\n\nPlease use the link below to select a slot "
            "before 25 July:\nhttps://calendly.com/shopee-uni/swe-intern\n\n"
            "Best regards,\nUniversity Recruiting Team",
        ),
        Category.INTERVIEW,
    ),
    (
        _email(
            "s2",
            "no-reply@hackerrank.com",
            "Your GovTech Online Assessment is ready",
            "Hello,\n\nYou have been invited to complete an online assessment for the "
            "Data Engineering Intern role at GovTech.\n\nThe assessment consists of 3 "
            "coding questions and takes approximately 90 minutes. You must complete it "
            "by 28 July 2026, 23:59 SGT.\n\nStart here: https://hackerrank.com/test/xyz",
        ),
        Category.OA,
    ),
    (
        _email(
            "s3",
            "careers@grab.com",
            "Update on your application — Backend Engineer Intern",
            "Dear Jayden,\n\nThank you for taking the time to speak with our team. "
            "After careful consideration, we have decided not to move forward with "
            "your application for the Backend Engineer Intern position at this time.\n\n"
            "We were genuinely impressed by your background and encourage you to apply "
            "again in future cycles.\n\nWarm regards,\nTalent Acquisition",
        ),
        Category.RESULT,
    ),
    (
        _email(
            "s4",
            "internships@stripe.com",
            "Offer — Software Engineering Intern, Singapore",
            "Hi Jayden,\n\nWe're delighted to offer you the Software Engineering Intern "
            "position for Summer 2027 in our Singapore office!\n\nYour formal offer "
            "letter is attached. Please review and respond by 5 August 2026.\n\n"
            "Congratulations, and welcome!\n\nStripe University Recruiting",
        ),
        Category.RESULT,
    ),
    (
        _email(
            "s5",
            "no-reply@greenhouse.io",
            "We've received your application — Product Analyst Intern",
            "Hi Jayden,\n\nThanks for applying to the Product Analyst Intern role at "
            "Sea Group. Your application is now under review.\n\nWe'll be in touch if "
            "your profile matches what we're looking for. No action is needed from you "
            "right now.",
        ),
        Category.OTHER,
    ),
    (
        _email(
            "s6",
            "newsletter@techinasia.com",
            "This week: an interview with the founder of Carousell",
            "Tech in Asia Weekly\n\nIn this issue: our exclusive interview with the "
            "founder of Carousell on scaling across Southeast Asia, plus the week's "
            "funding rounds and three startups hiring right now.\n\nRead online | "
            "Unsubscribe",
        ),
        Category.NOT_RELEVANT,
    ),
    (
        _email(
            "s7",
            "jobs@linkedin.com",
            "30+ new internships matching your profile",
            "Jobs picked for you\n\nSoftware Engineer Intern at DBS Bank — Singapore\n"
            "Data Science Intern at Visa — Singapore\nBackend Intern at Ninja Van\n\n"
            "See all 32 matches. You're receiving this because you have job alerts on.",
        ),
        Category.NOT_RELEVANT,
    ),
    (
        _email(
            "s8",
            "recruiting@janestreet.com",
            "Scheduling your final round — Quantitative Trading Intern",
            "Hi Jayden,\n\nCongratulations on clearing the second round. We'd like to "
            "schedule your final round, which will consist of two 30-minute sessions "
            "with members of the trading desk.\n\nCould you share your availability "
            "for the week of 4 August? We can accommodate SGT mornings.\n\nBest,\n"
            "Campus Recruiting",
        ),
        Category.INTERVIEW,
    ),
    (
        _email(
            "s9",
            "events@nus.edu.sg",
            "NUS Career Fair 2026 — register now to meet 80+ employers",
            "Dear Student,\n\nThe annual NUS Career Fair returns on 12 August at the "
            "University Town Green. Over 80 employers will be present, including many "
            "offering internship positions.\n\nRegistration is free but required. "
            "Bring copies of your resume.",
        ),
        Category.NOT_RELEVANT,
    ),
    (
        _email(
            "s10",
            "talent@gic.com.sg",
            "Documents required for your internship application",
            "Dear Jayden,\n\nFollowing your application for the Investment Analyst "
            "Intern role, we require the following before proceeding:\n\n"
            "1. Official academic transcript\n2. A copy of your NRIC\n"
            "3. Preferred internship start date\n\nPlease reply with these by 30 July.",
        ),
        Category.OTHER,
    ),
]


# --- Extraction expectations (Stage 4) -------------------------------------
#
# Keyed by message_id, covering only the samples the router keeps. Matching is
# deliberately loose: "Sea" and "Sea Group" are both correct, and pinning an
# exact string would turn a working extractor into a failing test. What is
# checked strictly is what a wrong answer would actually cost — the employer,
# the status, and whether a real deadline was found.
#
# s2 and s5 are the ones that matter most: both arrive from an intermediary
# (HackerRank, Greenhouse) while the employer is named only in the body, so a
# model that reads the sender domain gets them wrong.

@dataclass(frozen=True)
class ExtractionExpectation:
    company_any_of: tuple[str, ...]
    role_contains: str
    status: ApplicationStatus
    # None means "the email states no actionable date"; a date means the
    # extractor should find that day. Time of day is not checked.
    key_date: Optional[date] = None
    # Set where the email implies a date without committing to one, so either
    # answer is defensible.
    key_date_optional: bool = False


EXTRACTION_EXPECTATIONS: dict[str, ExtractionExpectation] = {
    "s1": ExtractionExpectation(
        company_any_of=("shopee",),
        role_contains="software engineer",
        status=ApplicationStatus.INTERVIEW,
        key_date=date(2026, 7, 25),
    ),
    "s2": ExtractionExpectation(
        # Sent by HackerRank; the employer is GovTech.
        company_any_of=("govtech",),
        role_contains="data engineer",
        status=ApplicationStatus.ASSESSMENT,
        key_date=date(2026, 7, 28),
    ),
    "s3": ExtractionExpectation(
        company_any_of=("grab",),
        role_contains="backend engineer",
        status=ApplicationStatus.REJECTED,
    ),
    "s4": ExtractionExpectation(
        company_any_of=("stripe",),
        role_contains="software engineering",
        status=ApplicationStatus.OFFER,
        key_date=date(2026, 8, 5),
    ),
    "s5": ExtractionExpectation(
        # Sent by Greenhouse; the employer is Sea Group.
        company_any_of=("sea", "sea group"),
        role_contains="product analyst",
        status=ApplicationStatus.APPLIED,
    ),
    "s8": ExtractionExpectation(
        company_any_of=("jane street",),
        role_contains="quantitative trading",
        status=ApplicationStatus.INTERVIEW,
        # Asks for availability during the week of 4 August rather than naming
        # a slot, so both "4 Aug" and "no date yet" are reasonable.
        key_date=date(2026, 8, 4),
        key_date_optional=True,
    ),
    "s10": ExtractionExpectation(
        company_any_of=("gic",),
        role_contains="investment analyst",
        status=ApplicationStatus.ACTION_NEEDED,
        key_date=date(2026, 7, 30),
    ),
}
