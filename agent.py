import os
import base64
import httpx
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
import logging

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# ── State ─────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    audio_data: bytes          # raw audio bytes
    file_name: str             # original file name
    transcript: str            # transcribed text
    summary: str               # short summary
    action_points: list[str]   # extracted action items
    error: str                 # error message if anything fails


# ── LLM ───────────────────────────────────────────────────────────────────────

llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
    google_api_key=GEMINI_API_KEY,
    temperature=0.3,
)


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def transcribe_node(state: AgentState) -> AgentState:
    """Transcribe audio using Gemini's native audio understanding."""
    try:
        audio_b64 = base64.b64encode(state["audio_data"]).decode("utf-8")

        # Detect mime type from file name
        fname = state.get("file_name", "audio.ogg").lower()
        if fname.endswith(".mp3"):
            mime = "audio/mpeg"
        elif fname.endswith(".wav"):
            mime = "audio/wav"
        elif fname.endswith(".m4a"):
            mime = "audio/mp4"
        elif fname.endswith(".mp4"):
            mime = "audio/mp4"
        else:
            mime = "audio/ogg"  # Telegram default

        message = HumanMessage(content=[
            {
                "type": "media",
                "data": audio_b64,
                "mime_type": mime,
            },
            {
                "type": "text",
                "text": (
                    "Please transcribe this audio accurately. "
                    "Preserve speaker tone and include all spoken words. "
                    "If multiple speakers exist, label them as Speaker 1, Speaker 2, etc. "
                    "Output ONLY the raw transcript, nothing else."
                ),
            },
        ])

        response = await llm.ainvoke([message])
        transcript = response.content.strip()
        logger.info(f"Transcription complete: {len(transcript)} chars")
        return {**state, "transcript": transcript, "error": ""}

    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return {**state, "transcript": "", "error": f"Transcription failed: {str(e)}"}


async def summarize_node(state: AgentState) -> AgentState:
    """Summarize the transcript into key points."""
    if state.get("error") or not state.get("transcript"):
        return state

    try:
        prompt = f"""You are a smart assistant summarizing a voice note or meeting recording.

TRANSCRIPT:
{state['transcript']}

Write a clear, concise summary in 3-5 sentences. 
Capture the main topic, key decisions, and overall tone.
Output ONLY the summary, nothing else."""

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        summary = response.content.strip()
        logger.info("Summary complete")
        return {**state, "summary": summary}

    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        return {**state, "error": f"Summarization failed: {str(e)}"}


async def extract_actions_node(state: AgentState) -> AgentState:
    """Extract action points from the transcript."""
    if state.get("error") or not state.get("transcript"):
        return state

    try:
        prompt = f"""You are a smart assistant extracting action items from a voice note or meeting.

TRANSCRIPT:
{state['transcript']}

Extract all action points, tasks, or follow-ups mentioned.
Format each as a short, clear task starting with a verb (e.g. "Send report to John").
If no action points exist, return exactly: NONE

Output ONLY a numbered list of action points, nothing else."""

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content.strip()

        if raw.upper() == "NONE" or not raw:
            actions = []
        else:
            actions = [
                line.lstrip("0123456789.-) ").strip()
                for line in raw.splitlines()
                if line.strip()
            ]

        logger.info(f"Extracted {len(actions)} action points")
        return {**state, "action_points": actions}

    except Exception as e:
        logger.error(f"Action extraction failed: {e}")
        return {**state, "action_points": [], "error": f"Action extraction failed: {str(e)}"}


# ── Router ─────────────────────────────────────────────────────────────────────

def should_continue(state: AgentState) -> str:
    if state.get("error"):
        return END
    return "continue"


# ── Build Graph ────────────────────────────────────────────────────────────────

def build_agent():
    graph = StateGraph(AgentState)

    graph.add_node("transcribe", transcribe_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("extract_actions", extract_actions_node)

    graph.set_entry_point("transcribe")

    graph.add_conditional_edges(
        "transcribe",
        should_continue,
        {"continue": "summarize", END: END},
    )
    graph.add_conditional_edges(
        "summarize",
        should_continue,
        {"continue": "extract_actions", END: END},
    )
    graph.add_edge("extract_actions", END)

    return graph.compile()


agent = build_agent()


# ── Public function ────────────────────────────────────────────────────────────

async def process_audio(audio_data: bytes, file_name: str = "audio.ogg") -> dict:
    """Run the full agent pipeline on audio bytes."""
    initial_state: AgentState = {
        "audio_data": audio_data,
        "file_name": file_name,
        "transcript": "",
        "summary": "",
        "action_points": [],
        "error": "",
    }

    result = await agent.ainvoke(initial_state)
    return result
