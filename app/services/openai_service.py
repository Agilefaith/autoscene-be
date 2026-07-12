import json
import math

from openai import AsyncOpenAI
from app.core.config import get_settings

settings = get_settings()
_client = AsyncOpenAI(api_key=settings.openai_api_key)

# Average spoken delivery rate (config-driven); used both to size generated
# scripts and to estimate how long an arbitrary script will take to read aloud.
WORDS_PER_MINUTE = settings.script_words_per_minute


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


# Shared voice for every script-writing call (single-shot and per-section).
_SYSTEM_BASE = (
    "You are an expert scriptwriter for faceless, narrated short-form videos. "
    "Write engaging, natural-sounding narration meant to be read by a voiceover. "
    "Output ONLY the script text — no scene directions, no annotations, no titles, "
    "no section headings or labels. "
    "Follow the STYLE, GOAL, and TONE precisely so the script clearly reflects each."
)


def _tokens_for(words: int) -> int:
    """Token budget for a target word count (~1.6 tokens/word), floored so short
    requests still get a usable ceiling."""
    return max(48, int(words * 1.6))


def _creative_brief(
    title: str, product_name: str, target_audience: str,
    tone: str, goal: str, style: str, niche: str,
) -> str:
    """The shared STYLE/GOAL/TONE/NICHE brief injected into every generation call.
    Falls back to the raw label for values without explicit guidance."""
    style_guidance = STYLE_GUIDANCE.get(style, f"{style} style.")
    goal_guidance = GOAL_GUIDANCE.get(goal, f"Optimise for: {goal}.")
    tone_desc = TONE_GUIDANCE.get(tone, tone)
    niche_guidance = NICHE_GUIDANCE.get(niche, f"{niche}." if niche else "")
    niche_line = f"NICHE — {niche}: {niche_guidance}\n" if niche_guidance else ""
    return (
        f"Video title concept: {title}\n"
        f"Product/Service: {product_name}\n"
        f"Target audience: {target_audience}\n\n"
        f"{niche_line}"
        f"STYLE — {style}: {style_guidance}\n"
        f"GOAL — {goal}: {goal_guidance}\n"
        f"TONE — keep it {tone_desc} throughout.\n"
    )


async def _complete(system: str, user: str, *, max_tokens: int, temperature: float = 0.7) -> str:
    response = await _client.chat.completions.create(
        model=settings.script_gen_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


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
    """Generate a narration script sized to the requested duration.

    Short targets use a single completion; long targets (> chunk threshold) use an
    outline→expand multi-call strategy because one gpt-4o-mini completion self-
    terminates after a few hundred words and cannot reliably fill long durations.
    """
    target_duration_seconds = max(1, min(target_duration_seconds, settings.max_video_seconds))
    target_words = max(8, int((target_duration_seconds / 60) * WORDS_PER_MINUTE))
    lo, hi = int(target_words * 0.85), int(target_words * 1.10)
    brief = _creative_brief(title, product_name, target_audience, tone, goal, style, niche)

    if target_duration_seconds <= settings.script_chunk_threshold_seconds:
        return await _generate_single(brief, target_duration_seconds, target_words, lo, hi)
    return await _generate_chunked(brief, target_duration_seconds, target_words, lo)


async def _generate_single(brief: str, target_seconds: int, target_words: int, lo: int, hi: int) -> str:
    system = _SYSTEM_BASE + (
        " Match the requested length: write enough to fill the target duration and "
        "do not stop early, but do not pad or ramble past it."
    )
    user = (
        "Write a script for a narrated video.\n"
        f"{brief}\n"
        f"Length: write between {lo} and {hi} words (~{target_seconds}s spoken at "
        f"{WORDS_PER_MINUTE} words/min). Aim for the middle of that range — do not stop "
        f"early and do not exceed {hi} words.\n"
        "Start immediately with the hook. No intro like 'Here is your script'."
    )
    return await _complete(system, user, max_tokens=_tokens_for(target_words))


async def _outline(brief: str, target_seconds: int, n_sections: int) -> list[dict]:
    """Plan a sequential list of narration beats for a long script."""
    system = (
        "You are a scriptwriting planner for narrated videos. Given a creative brief, "
        "produce a sequential outline of narration beats that together tell one coherent, "
        "well-paced piece. Output valid JSON only."
    )
    user = (
        "Creative brief:\n"
        f"{brief}\n"
        f"Plan EXACTLY {n_sections} sequential beats for a ~{target_seconds}s narrated video. "
        "Each beat is one movement of the narration, in order, each building on the one before "
        "so the whole reads as a single continuous script.\n"
        'Output JSON: {"beats": [{"title": str, "direction": str}]} where "direction" is one '
        "sentence describing what that beat covers. Output JSON only, no markdown."
    )
    response = await _client.chat.completions.create(
        model=settings.script_gen_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.6,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content or "{}")
    beats = [b for b in (data.get("beats") or []) if isinstance(b, dict)]
    # Fallback: if the model returned nothing usable, expand generic beats so the
    # chunked path still produces a full-length script.
    if not beats:
        beats = [{"title": "", "direction": ""} for _ in range(n_sections)]
    return beats


async def _expand_section(
    brief: str, beat: dict, idx: int, total: int, words_per_section: int, prior: list[str],
) -> str:
    """Write one section of narration, seamlessly continuing what came before."""
    tail = prior[-1][-600:] if prior else ""
    continuity = (
        f"The script so far ends with:\n...{tail}\n\nContinue seamlessly — do not repeat it.\n"
        if tail else "This is the opening of the script.\n"
    )
    title = (beat.get("title") or "").strip()
    direction = (beat.get("direction") or "").strip()
    beat_line = f"This section — {title}: {direction}\n" if (title or direction) else ""
    lo_s, hi_s = int(words_per_section * 0.85), int(words_per_section * 1.15)
    closing = (
        "This is the FINAL section — bring it to a satisfying close. "
        if idx == total - 1 else "Do not conclude yet; keep the momentum going. "
    )
    opening = "Open with a strong scroll-stopping hook. " if idx == 0 else ""
    system = _SYSTEM_BASE + (
        " You are writing ONE section of a longer continuous script; write only this "
        "section's narration so it flows naturally from the previous text."
    )
    user = (
        "Creative brief:\n"
        f"{brief}\n"
        f"{continuity}"
        f"{beat_line}"
        f"Write section {idx + 1} of {total}: between {lo_s} and {hi_s} words of pure narration. "
        f"{opening}{closing}"
        "Output only the narration text — no headings, labels, or list markers."
    )
    return await _complete(system, user, max_tokens=_tokens_for(words_per_section))


async def _extend(brief: str, script: str, lo: int) -> str:
    """Append narration when the assembled script falls short of the word floor."""
    deficit = max(40, lo - len(script.split()))
    tail = script[-800:]
    system = _SYSTEM_BASE + (
        " You are continuing an existing script; add only new narration that flows on."
    )
    user = (
        "Creative brief:\n"
        f"{brief}\n"
        f"The script so far ends with:\n...{tail}\n\n"
        f"Continue it seamlessly with about {deficit} more words of narration that develop the "
        "topic further — no repetition, and do not exceed the natural end of the piece.\n"
        "Output only the new narration text."
    )
    addition = await _complete(system, user, max_tokens=_tokens_for(deficit))
    return f"{script}\n\n{addition}".strip() if addition else script


async def _generate_chunked(brief: str, target_seconds: int, target_words: int, lo: int) -> str:
    """Outline→expand generation for long scripts, with a word-floor safety net."""
    n_sections = max(2, math.ceil(target_words / settings.script_words_per_section))
    words_per_section = math.ceil(target_words / n_sections)

    beats = await _outline(brief, target_seconds, n_sections)
    parts: list[str] = []
    for i, beat in enumerate(beats):
        section = await _expand_section(brief, beat, i, len(beats), words_per_section, parts)
        if section:
            parts.append(section)

    script = "\n\n".join(parts).strip()
    for _ in range(settings.script_max_floor_iterations):
        if len(script.split()) >= lo:
            break
        script = await _extend(brief, script, lo)
    return script
