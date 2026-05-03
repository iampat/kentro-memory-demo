"""Generate the synthetic demo corpus via Anthropic.

Reads `ANTHROPIC_API_KEY`. Writes 6 markdown files into `examples/synthetic_corpus/`
per the contract in `demo.md` § Synthetic Corpus Design. Idempotent: skips files
that already exist on disk so re-runs are free, and the output is meant to be
committed to git so most contributors never need to re-run this.

Each document is generated with a structured prompt that:
- pins the persona ("You are writing a meeting transcript snippet")
- pins the length and format
- pins the required facts that must appear verbatim (the conflict scenario depends on
  the specific dollar figures and names)
- gives a stylistic seed

Usage:
    uv run python scripts/generate_corpus.py
    uv run python scripts/generate_corpus.py --force   # re-generate everything
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import anthropic

from kentro_server.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "examples" / "synthetic_corpus"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1200


@dataclass(frozen=True)
class DocSpec:
    filename: str
    prompt: str


_PROMPTS: tuple[DocSpec, ...] = (
    DocSpec(
        filename="acme_call_2026-04-15.md",
        prompt=(
            "Write a meeting-transcript snippet for a sales call dated 2026-04-15 between "
            "Jane Doe (head of procurement at Acme Corp) and a sales rep named Sam at Kentro. "
            "Length ~400-600 words of natural conversational dialogue with hedges and asides. "
            "Format as Markdown with a small frontmatter block (## Acme Corp - sales call - 2026-04-15) "
            "and dialogue lines like '**Jane:** ...' and '**Sam:** ...'. "
            "Required facts that MUST appear verbatim: the date 2026-04-15, the figure $250K, "
            "Jane saying she will 'confirm with finance' before locking the renewal number, "
            "and Acme Corp as the customer name. Treat the $250K as Jane's verbal floated number "
            "and the renewal value being discussed. "
            "HARD CONSTRAINTS — these are mandatory and the transcript will be rejected if violated: "
            "1) The ONLY dollar figure that appears anywhere in the transcript is $250K. "
            "Do NOT mention any other dollar amount or price range (no $300K, no $350K, no other figures). "
            "2) Sam must NOT quote a specific price, range, or dollar number. Sam can talk about "
            "scope, proposal next steps, follow-up materials — but never a number. "
            "3) Output plain Markdown text directly, NOT wrapped in a ```markdown code fence. "
            "Tone: realistic enterprise-sales conversation."
        ),
    ),
    DocSpec(
        filename="email_jane_2026-04-17.md",
        prompt=(
            "Write a follow-up email from Jane Doe at Acme Corp to Sam at Kentro, dated "
            "2026-04-17. Length ~150-250 words, professional tone. "
            "Format: a Markdown header block (## Email - Jane Doe -> Sam, 2026-04-17), "
            "then 'From:', 'To:', 'Subject:', 'Date:' lines, then the email body in plain prose. "
            "Required facts that MUST appear verbatim: the dates 2026-04-17 and a reference to "
            "the Monday call (2026-04-15), the figure $300K, the phrase 'after speaking with finance', "
            "Jane Doe's name, Acme Corp. The email must explicitly contradict the verbal $250K from "
            "the Monday call by revising the renewal to $300K. Output only the markdown content."
        ),
    ),
    DocSpec(
        filename="acme_ticket_142.md",
        prompt=(
            "Write a customer-service ticket #142 for Acme Corp, ~80-150 words. "
            "Format: Markdown with a header (## Ticket #142 - Acme Corp), then fields "
            "(Status: open, Severity: medium, Reported by: <a fictional Acme employee name>, "
            "Date: 2026-04-19, Subject: <short>), then a Description section with one or two "
            "paragraphs describing a realistic SaaS issue (e.g., dashboard latency, login flakiness). "
            "Output only the markdown content."
        ),
    ),
    DocSpec(
        filename="acme_ticket_157.md",
        prompt=(
            "Write a customer-service ticket #157 for Acme Corp, ~80-150 words, on a different "
            "issue from ticket #142. Format: same as #142 (## header, fields, description) with "
            "Status: open, a different Severity (low or high), Date: 2026-04-21, and a different "
            "Subject (e.g., billing question, API timeout, export bug). Output only the markdown content."
        ),
    ),
    DocSpec(
        filename="acme_ticket_162.md",
        prompt=(
            "Write a customer-service ticket #162 for Acme Corp, ~80-150 words, on yet another "
            "topic from #142 and #157. Same Markdown format. Status: open, Date: 2026-04-23. "
            "Pick a third realistic SaaS issue. Output only the markdown content."
        ),
    ),
    DocSpec(
        filename="internal_slack_thread_2026-04-19.md",
        prompt=(
            "Write an internal Slack thread between two account executives at Kentro discussing "
            "the Acme Corp deal, dated 2026-04-19. Length ~200-300 words. "
            "Format: Markdown header (## Slack - #aes - 2026-04-19), then a short context line, "
            "then thread messages like '**sam.r** [10:14]' followed by the message body, "
            "alternating between two AEs (sam.r and priya.k). At least one message must mention "
            "Acme Corp by name and discuss the discrepancy between the Monday call's $250K and "
            "Jane's email at $300K. Tone: casual internal chat. Output only the markdown content."
        ),
    ),
    DocSpec(
        filename="ali_meeting_note_2026-03-10.md",
        prompt=(
            "Write a meeting note from a 1:1 conversation about Ali (the kentro CEO) dated "
            "2026-03-10. Length ~100-150 words. Format: a Markdown header (## Meeting note - "
            "2026-03-10 - Ali sync), then a short bulleted set of action items / topics. "
            "The note MUST include the phrase 'Phone: 778-968-1361' verbatim somewhere "
            "(treat it as Ali's contact info captured in the note). Do NOT include an email "
            "address. Output only the markdown content."
        ),
    ),
    DocSpec(
        filename="ali_meeting_note_2026-04-02.md",
        prompt=(
            "Write a different meeting note about Ali (same person from the previous note) dated "
            "2026-04-02. Length ~100-150 words. Format: same Markdown shape as the 2026-03-10 note. "
            "This note MUST include the phrase 'Email: ali@kentro.ai' verbatim somewhere. "
            "Do NOT include a phone number. The two notes together let strict-key entity "
            "resolution merge into one Person.Ali record with phone from the first note and "
            "email from this one. Output only the markdown content."
        ),
    ),
)


def _generate(client: anthropic.Anthropic, prompt: str, model: str) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return _strip_outer_code_fence("\n".join(parts).strip()) + "\n"


def _strip_outer_code_fence(text: str) -> str:
    """Some prompts cause the LLM to wrap the entire response in ```markdown ... ```.

    Detect that case and strip the wrapping fence so consumers see clean markdown.
    """
    lines = text.splitlines()
    if len(lines) < 2:
        return text
    first = lines[0].strip()
    last = lines[-1].strip()
    if first.startswith("```") and last == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="regenerate even if files exist")
    args = parser.parse_args()

    settings = Settings()
    if not settings.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY not set (checked .env and environment)", file=sys.stderr)
        return 2

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    model = settings.kentro_llm_smart_model
    if not model.startswith("claude-"):
        print(
            f"ERROR: corpus generator only supports Anthropic; smart model is {model!r}",
            file=sys.stderr,
        )
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    for spec in _PROMPTS:
        path = OUT_DIR / spec.filename
        if path.exists() and not args.force:
            print(f"  skip   {spec.filename} (already exists)")
            skipped += 1
            continue
        print(f"  gen    {spec.filename} ...", flush=True)
        text = _generate(client, spec.prompt, model)
        path.write_text(text)
        print(f"  wrote  {spec.filename} ({len(text)} chars)")
        written += 1

    print(f"\n{written} written, {skipped} skipped → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
