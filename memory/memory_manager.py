import os
import json
import re
from typing import Any, Dict, Optional
from profile import Profile 

MEMORY_FILE = "memory.json"

DEFAULT_MEMORY = {
    "name": "",
    "preferred_name": "",
    "age": "",
    "education": {
        "level": "",
        "college_or_school": "",
        "course_or_branch": ""
    },
    "location": "",
    "skills": [],
    "projects": [],
    "device_preferences": [],
    "language_preferences": [],
    "study_goals": [],
    "work_or_career_goals": [],
    "hobbies_or_interests": [],
    "writing_preferences": [],
    "other_safe_notes": [],
    "conflicts_or_uncertainties": []
}


def ensure_memory_file() -> None:
    """Create memory.json if it doesn't exist."""
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_MEMORY, f, indent=4, ensure_ascii=False)


def load_memory() -> Dict[str, Any]:
    """Load memory from file, creating it if needed."""
    ensure_memory_file()

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass

    return DEFAULT_MEMORY.copy()


def save_memory(memory: Dict[str, Any]) -> None:
    """Save memory to file."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=4, ensure_ascii=False)


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON object from model response text."""
    if not text:
        return None

    cleaned = text.strip()

    # Remove markdown fences if present
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    # Try direct JSON parse
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Try finding first JSON object in the text
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None


def merge_lists(old_list, new_list):
    """Merge lists without duplicates while preserving order."""
    result = []
    seen = set()

    for item in old_list + new_list:
        if isinstance(item, str):
            key = item.strip().lower()
        else:
            key = str(item).strip().lower()

        if key and key not in seen:
            seen.add(key)
            result.append(item)

    return result


def deep_merge_memory(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Merge new memory into existing memory."""
    merged = old.copy()

    for key, value in new.items():
        if key == "education" and isinstance(value, dict):
            if "education" not in merged or not isinstance(merged.get("education"), dict):
                merged["education"] = {}

            for edu_key, edu_value in value.items():
                if edu_value not in ("", None, [], {}):
                    merged["education"][edu_key] = edu_value

        elif isinstance(value, list):
            old_value = merged.get(key, [])
            if not isinstance(old_value, list):
                old_value = []
            merged[key] = merge_lists(old_value, value)

        elif isinstance(value, dict):
            old_value = merged.get(key, {})
            if not isinstance(old_value, dict):
                old_value = {}
            merged[key] = deep_merge_memory(old_value, value)

        elif value not in ("", None):
            merged[key] = value

    return merged

def build_memory_prompt(current_memory: Dict[str, Any], user_message: str) -> str:
    """Build the hidden memory extraction prompt."""
    return f"""
You are a careful user-profile extraction assistant.

Your task is to read the provided user information and extract only useful, non-sensitive personal details that can help personalize future conversations.

Follow these rules:

1. Extract only information that is explicitly stated by the user.
2. Do not guess, infer, or fill in missing details.
3. Do not extract or store sensitive personal information unless the user clearly and explicitly asks you to save it.
4. Never infer sensitive traits such as religion, race, caste, political views, sexual orientation, health conditions, or exact private location.
5. If a detail is uncertain, mark it as "unknown" or omit it.
6. Prefer long-term stable facts over temporary facts.
7. Keep the output clean, structured, and easy to reuse later.

Extract information only from these safe categories when available:
- Name or preferred name
- Age, only if explicitly mentioned
- General location, such as country or city, only if the user clearly shares it
- Education level
- School, college, branch, or course
- Work or project interests
- Technical skills and tools
- Device or software preferences
- Language preferences
- Study goals
- Long-term goals
- Non-sensitive hobbies and interests
- Writing style preferences
- Communication preferences
- Important reminders the user explicitly wants remembered

Do not include:
- Exact address
- Phone number
- Passwords
- OTPs
- Bank details
- Sensitive identity traits
- Medical or mental health details
- Political opinions
- Private relationship details
- Any information that feels unnecessarily personal or invasive

If the user provides conflicting details at different times, keep the most recent explicit version and note the conflict briefly.

Current Memory:
{json.dumps(current_memory, indent=2, ensure_ascii=False)}

Latest User Message:
{user_message}

Return ONLY the updated fields as valid JSON.
If nothing should be remembered, return {{}}.
Do not include explanations, markdown, or code fences.
"""


def update_memory_from_response(raw_response: str) -> Dict[str, Any]:
    """Parse model response and merge into memory.json."""
    current_memory = load_memory()
    new_memory = extract_json_from_text(raw_response)

    if not new_memory:
        return current_memory

    merged = deep_merge_memory(current_memory, new_memory)
    save_memory(merged)
    return merged


def update_memory_from_user_message(client, user_message: str, model: str = "openai/gpt-oss-20b:free") -> Dict[str, Any]:
    current_memory = load_memory()
    prompt = build_memory_prompt(current_memory, user_message)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": Profile()},
                {"role": "user", "content": prompt}
            ]
        )

        if not response or not getattr(response, "choices", None):
            return current_memory

        first_choice = response.choices[0] if len(response.choices) > 0 else None
        if not first_choice or not getattr(first_choice, "message", None):
            return current_memory

        raw_text = getattr(first_choice.message, "content", "") or ""
        if not raw_text.strip():
            return current_memory

        return update_memory_from_response(raw_text)

    except Exception:
        return current_memory