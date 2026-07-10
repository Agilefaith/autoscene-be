from openai import AsyncOpenAI
from app.core.config import get_settings

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)

# Average spoken delivery rate; used both to size generated scripts and to
# estimate how long an arbitrary script will take to read aloud.
WORDS_PER_MINUTE = 130


def estimate_duration_seconds(script: str) -> int:
    """Estimate spoken duration of a script from its word count (≥1s)."""
    words = len((script or "").split())
    return max(1, round(words / WORDS_PER_MINUTE * 60))


async def describe_reference_image(image_url: str) -> str:
    """Build a fixed, detailed character description from a reference image via
    GPT-Vision. Reused verbatim across every scene's image prompt for best-effort
    character consistency on SDXL. Returns "" on any failure (non-fatal)."""
    if not image_url:
        return ""
    try:
        resp = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "Describe the main character or subject in this reference image for a "
                        "text-to-image model, as ONE dense sentence: apparent age, gender, hair, "
                        "skin tone, distinctive facial features, build, clothing, and color "
                        "palette. Physical description only, no background or scene, no names, "
                        "no preamble."
                    )},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            temperature=0.2,
            max_tokens=140,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


# Explicit guidance so each Style/Goal/Tone choice produces a visibly distinct,
# consistent script — not just a free-text label dropped into the prompt.
STYLE_GUIDANCE: dict[str, str] = {
    "storytelling":  "Tell it as a story: a hook, a turn, and a satisfying payoff; vivid, immersive narration.",
    "educational":   "Clear explainer voice: teach one idea simply, step by step, so anyone understands by the end.",
    "documentary":   "Informative narrated documentary style: calm, credible, explanatory; measured pace.",
    "punchy":        "Open with a scroll-stopping hook. Very short punchy sentences, fast pace, high energy.",
    "conversational":"Relaxed, relatable narration, like explaining something to a friend; natural and warm.",
}

GOAL_GUIDANCE: dict[str, str] = {
    "sales":        "Drive a purchase: highlight the single biggest benefit and end with a clear, specific call to action.",
    "engagement":   "Maximize watch-through and interaction: spark curiosity, invite a reaction or comment, keep it shareable.",
    "education":    "Teach one clear idea: explain it simply and leave the viewer with one concrete takeaway.",
    "storytelling": "Tell a tiny story: hook, a turn, and a satisfying payoff; make it feel personal.",
}

TONE_GUIDANCE: dict[str, str] = {
    "friendly":      "warm and approachable",
    "professional":  "polished and credible",
    "energetic":     "high-energy and upbeat",
    "authoritative": "confident and expert",
    "casual":        "relaxed and conversational",
    "inspiring":     "uplifting and motivating",
}


# Per-niche guidance so the AI script actually adopts the niche's subject matter,
# vocabulary, and narrative voice (matches the niches in schemas/common.py).
NICHE_GUIDANCE: dict[str, str] = {
    "Bible storytelling":            "Retell a Bible story or biblical theme with reverent, vivid narration.",
    "Finance storytelling":          "Explain a money or finance idea through a concrete, relatable story.",
    "History":                       "Recount a historical event or figure with cinematic, factual narration.",
    "Psychology":                    "Explore a psychological concept and how it shows up in everyday life.",
    "Relatable Life Storytelling":   "Tell a small, relatable slice-of-life story the viewer sees themselves in.",
    "Money & Online Income":         "Share a practical online-income or money insight, grounded and no-hype.",
    "Self-Improvement & Discipline": "Motivate around discipline, habits, and self-improvement, firm but encouraging.",
    "Psychology & Human Behavior":   "Reveal a surprising truth about human behavior and why we act that way.",
    "Dating & Relationships":        "Give an honest, mature take on dating or relationships.",
    "Dark Truths / Reality":         "Deliver a sober, thought-provoking dark truth about life or society.",
    "Motivational & Quote Shorts":   "Punchy, high-impact motivation built around a powerful central idea.",
    "Educational Explainers":        "Clearly explain one concept so anyone understands it by the end.",
    "What If / Hypothetical Scenarios": "Explore an intriguing 'what if' scenario and its consequences.",
}


async def generate_script(
    title: str,
    product_name: str,
    target_audience: str,
    tone: str,
    goal: str,
    style: str,
    niche: str = "",
    target_duration_seconds: int = 30,
) -> str:
    words_per_minute = WORDS_PER_MINUTE
    target_words = max(8, int((target_duration_seconds / 60) * words_per_minute))
    lo, hi = int(target_words * 0.85), int(target_words * 1.10)
    # Hard token ceiling (~1.3 tokens/word) so the model physically can't ramble
    # to 2x the requested length — the root cause of 15s requests rendering ~30s.
    max_tokens = max(48, int(target_words * 1.6))

    # Resolve explicit guidance; fall back to the raw label for unknown values.
    style_guidance = STYLE_GUIDANCE.get(style, f"{style} style.")
    goal_guidance = GOAL_GUIDANCE.get(goal, f"Optimise for: {goal}.")
    tone_desc = TONE_GUIDANCE.get(tone, tone)
    niche_guidance = NICHE_GUIDANCE.get(niche, f"{niche}." if niche else "")

    system = (
        "You are an expert scriptwriter for faceless, narrated short-form videos. "
        "Write engaging, natural-sounding narration meant to be read by a voiceover. "
        "Output ONLY the script text — no scene directions, no annotations, no titles. "
        "Follow the STYLE, GOAL, and TONE precisely so the script clearly reflects each. "
        "Length discipline is critical: the script is read aloud at ~130 words/min, "
        "so it MUST fit the requested duration. Be concise and punchy."
    )
    niche_line = f"NICHE — {niche}: {niche_guidance}\n" if niche_guidance else ""
    user = (
        f"Write a script for a short-form video.\n"
        f"Video title concept: {title}\n"
        f"Product/Service: {product_name}\n"
        f"Target audience: {target_audience}\n\n"
        f"{niche_line}"
        f"STYLE — {style}: {style_guidance}\n"
        f"GOAL — {goal}: {goal_guidance}\n"
        f"TONE — keep it {tone_desc} throughout.\n\n"
        f"Length: {lo}–{hi} words MAXIMUM (~{target_duration_seconds}s spoken). "
        f"Do NOT exceed {hi} words.\n"
        "Start immediately with the hook. No intro like 'Here is your script'."
    )

    response = await _client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.7,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()
