import os
import json
import re

from dotenv import load_dotenv
import streamlit as st
from openai import OpenAI

from agents.chat_agent import Chat_agent
from agents.coding_agent import Coding_agent
from agents.math_agent import Math_agent
from agents.study_agent import Study_agent
from agents.writer_agent import Writer_agent
from memory.memory_manager import update_memory_from_user_message, load_memory

try:
    import tiktoken
except ImportError:
    tiktoken = None


# Must be the first Streamlit command
st.set_page_config(page_title="Zord", layout="wide")

load_dotenv()

api_key = os.getenv("API_KEY") or os.getenv("OPENROUTER_API_KEY")
if not api_key:
    st.error("API key not found. Add API_KEY or OPENROUTER_API_KEY in your .env file.")
    st.stop()

# Initialize OpenAI client via OpenRouter
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

AGENTS = {
    "Chat": Chat_agent(),
    "Coding": Coding_agent(),
    "Math": Math_agent(),
    "Writer": Writer_agent(),
    "Study": Study_agent(),
}

AGENT_MODELS = {
    "Chat": "openai/gpt-oss-20b:free",
    "Coding": "cohere/north-mini-code:free",
    "Math": "nvidia/nemotron-nano-9b-v2:free",
    "Writer": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "Study": "openai/gpt-oss-120b:free",
}

BACKUP_MODELS = ["openai/gpt-oss-20b:free", "openai/gpt-oss-120b:free"]
AGENT_ORDER = ["Chat", "Coding", "Math", "Writer", "Study"]

MODEL_TOKEN_LIMIT = 131072
RESERVE_OUTPUT_TOKENS = 12000
SAFE_INPUT_LIMIT = MODEL_TOKEN_LIMIT - RESERVE_OUTPUT_TOKENS


if "agent_chats" not in st.session_state:
    st.session_state.agent_chats = {agent: [] for agent in AGENT_ORDER}

if "selected_agent" not in st.session_state:
    st.session_state.selected_agent = "Chat"

if "generated_files" not in st.session_state:
    st.session_state.generated_files = {}


def estimate_tokens(text: str) -> int:
    """Estimate tokens for a text string."""
    if not text:
        return 0

    if tiktoken is not None:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass

    # Fallback rough estimate: 1 token ~= 4 chars
    return max(1, len(text) // 4)


def count_messages_tokens(messages) -> int:
    """Count approximate tokens in message list."""
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("role", ""))
        total += estimate_tokens(msg.get("content", ""))
    return total


def trim_messages_to_limit(system_prompt: str, messages, limit: int):
    """
    Keep the newest messages while staying under token limit.
    Always keeps the system prompt separately.
    """
    trimmed = []
    total = estimate_tokens(system_prompt)

    for msg in reversed(messages):
        msg_tokens = estimate_tokens(msg.get("role", "")) + estimate_tokens(msg.get("content", ""))
        if total + msg_tokens > limit:
            break
        trimmed.append(msg)
        total += msg_tokens

    trimmed.reverse()
    return trimmed, total


def build_messages_for_model(system_prompt: str, messages):
    """Build a safe message list within token budget."""
    trimmed_messages, used_tokens = trim_messages_to_limit(
        system_prompt,
        messages,
        SAFE_INPUT_LIMIT,
    )
    return [{"role": "system", "content": system_prompt}] + trimmed_messages, used_tokens


def extract_json_from_text(text: str):
    """Try to extract JSON from raw text."""
    if not text:
        return None

    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None

    return None


def stream_with_auto_continue(
    system_prompt: str,
    messages,
    model="openai/gpt-oss-120b:free",
    max_rounds=4,
    placeholder=None,
):
    """
    Stream response from the LLM and automatically continue if the model stops due to length.
    """
    full_response = ""
    working_messages = [{"role": "system", "content": system_prompt}] + messages[:]

    for _ in range(max_rounds):
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=working_messages,
                stream=True,
            )

            round_text = ""
            finish_reason = None

            for chunk in stream:
                choice = chunk.choices[0]
                delta = ""

                if hasattr(choice, "delta") and choice.delta and choice.delta.content:
                    delta = choice.delta.content

                if delta:
                    round_text += delta
                    full_response += delta
                    if placeholder is not None:
                        placeholder.markdown(full_response)

                if hasattr(choice, "finish_reason") and choice.finish_reason:
                    finish_reason = choice.finish_reason

        except Exception as e:
            st.error(f"Streaming error: {e}")
            break

        if placeholder is not None:
            placeholder.markdown(full_response)

        if finish_reason != "length":
            break

        working_messages.append({"role": "assistant", "content": round_text})
        working_messages.append(
            {
                "role": "user",
                "content": "Continue immediately from where you stopped. Do not repeat previous text.",
            }
        )

    return full_response.strip()


def build_system_prompt(selected_agent: str) -> str:
    memory = load_memory()
    memory_text = json.dumps(memory, indent=2, ensure_ascii=False)

    return f"""
{AGENTS[selected_agent]}

Known User Memory:
{memory_text}

Use this information only if it is relevant.
Do not mention memory unless the user asks.
""".strip()


def get_response_for_agent(selected_agent: str, messages, placeholder=None) -> tuple[str, str]:
    system_prompt = build_system_prompt(selected_agent)
    model_messages, used_tokens = build_messages_for_model(system_prompt, messages)

    selected_model = AGENT_MODELS[selected_agent]
    models_to_try = [selected_model] + [m for m in BACKUP_MODELS if m != selected_model]

    last_error = None
    response_text = None

    for model in models_to_try:
        try:
            if placeholder is not None:
                placeholder.info(f"Trying model: {model}")

            response_text = stream_with_auto_continue(
                system_prompt=system_prompt,
                messages=model_messages[1:],
                model=model,
                max_rounds=4,
                placeholder=placeholder,
            )
            if response_text:
                break
        except Exception as e:
            last_error = e
            st.warning(f"Model {model} failed: {e}")

    if response_text is None:
        response_text = f"All models failed.\n\nLast error: {last_error}"

    return response_text, system_prompt


with st.sidebar:
    st.title("Zord")

    selected_agent = st.selectbox(
        "Choose Agent",
        AGENT_ORDER,
        index=AGENT_ORDER.index(st.session_state.selected_agent),
    )

    st.markdown("---")
    st.subheader("About")
    st.write("A multi-agent AI chatbot built with Streamlit.")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("🗑️ Clear This Agent", use_container_width=True):
            st.session_state.agent_chats[selected_agent] = []
            st.rerun()

    with col2:
        if st.button("🧹 Clear All", use_container_width=True):
            st.session_state.agent_chats = {agent: [] for agent in AGENT_ORDER}
            st.session_state.generated_files = {}
            st.rerun()

st.session_state.selected_agent = selected_agent

st.title(f"Zord — {selected_agent} Agent")
st.write("Ask me anything!")

messages = st.session_state.agent_chats[selected_agent]

# Show previous chat history
for message in messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

system_prompt_preview = build_system_prompt(selected_agent)
_, used_tokens = build_messages_for_model(system_prompt_preview, messages)
st.caption(f"Estimated tokens in current conversation: {used_tokens:,} / {SAFE_INPUT_LIMIT:,}")

user_input = st.chat_input(f"Type your message to {selected_agent}...")

if user_input:
    messages.append({"role": "user", "content": user_input})

    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        assistant_placeholder = st.empty()
        with st.spinner("Thinking..."):
            response_text, _ = get_response_for_agent(
                selected_agent=selected_agent,
                messages=messages,
                placeholder=assistant_placeholder,
            )
        ai_message = response_text.strip() if response_text else "No response received."
        assistant_placeholder.markdown(ai_message)

    messages.append({"role": "assistant", "content": ai_message})

    try:
        update_memory_from_user_message(client, user_input)
    except Exception as e:
        st.warning(f"Memory update skipped: {e}")
