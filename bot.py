import os
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction
from agent import process_audio
from database import get_user, create_user, increment_usage, is_premium
import logging

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
FREE_DAILY_LIMIT = 3


# ── Helpers ────────────────────────────────────────────────────────────────────

def format_result(result: dict) -> str:
    """Format agent result into a clean Telegram message."""
    lines = []

    lines.append("🎙 *Voice Note Summary*")
    lines.append("─────────────────────")

    if result.get("summary"):
        lines.append("\n📋 *Summary*")
        lines.append(result["summary"])

    if result.get("transcript"):
        # Show truncated transcript
        transcript = result["transcript"]
        if len(transcript) > 600:
            transcript = transcript[:600] + "..."
        lines.append("\n📝 *Transcript*")
        lines.append(f"_{transcript}_")

    if result.get("action_points"):
        lines.append("\n✅ *Action Points*")
        for i, action in enumerate(result["action_points"], 1):
            lines.append(f"{i}. {action}")
    else:
        lines.append("\n✅ *Action Points*")
        lines.append("No action points found.")

    return "\n".join(lines)


async def download_audio(bot, file_id: str) -> tuple[bytes, str]:
    """Download audio file from Telegram."""
    file = await bot.get_file(file_id)
    file_path = file.file_path
    file_name = file_path.split("/")[-1]

    async with httpx.AsyncClient() as client:
        response = await client.get(file_path)
        response.raise_for_status()

    return response.content, file_name


# ── Handlers ───────────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await create_user(user.id, user.first_name, user.username)

    text = (
        f"👋 Hey *{user.first_name}*\\!\n\n"
        "I'm your *Voice Note Summarizer*\\. Send me any voice note or audio file "
        "and I'll instantly give you:\n\n"
        "📋 A clear summary\n"
        "📝 The full transcript\n"
        "✅ Action points extracted\n\n"
        f"*Free plan:* {FREE_DAILY_LIMIT} summaries per day\n\n"
        "Just send a voice note to get started\\! 🎙"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎙 *How to use:*\n\n"
        "1\\. Record or forward any voice note\n"
        "2\\. Send it to me\n"
        "3\\. Get your summary in seconds\\!\n\n"
        "*Supported formats:* Voice notes, MP3, WAV, M4A, MP4 audio\n\n"
        "*Commands:*\n"
        "/start \\- Welcome message\n"
        "/help \\- This message\n"
        "/usage \\- Check your daily usage\n"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


async def usage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)

    if not user:
        await update.message.reply_text("Send /start first to get started.")
        return

    used = user.get("daily_usage", 0)
    premium = await is_premium(user_id)

    if premium:
        text = f"✨ *Premium user* — Unlimited summaries\nUsed today: *{used}*"
    else:
        remaining = max(0, FREE_DAILY_LIMIT - used)
        text = (
            f"📊 *Your Usage Today*\n\n"
            f"Used: *{used}/{FREE_DAILY_LIMIT}*\n"
            f"Remaining: *{remaining}*\n\n"
            f"Resets at midnight UTC."
        )

    await update.message.reply_text(text, parse_mode="Markdown")


async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Ensure user exists
    await create_user(user_id, user.first_name, user.username)

    # Check usage limit
    db_user = await get_user(user_id)
    premium = await is_premium(user_id)
    daily_usage = db_user.get("daily_usage", 0) if db_user else 0

    if not premium and daily_usage >= FREE_DAILY_LIMIT:
        await update.message.reply_text(
            f"⚠️ You've used all *{FREE_DAILY_LIMIT} free summaries* for today.\n\n"
            "Your limit resets at midnight UTC.\n\n"
            "Want unlimited summaries? Upgrade to Premium! 🚀",
            parse_mode="Markdown",
        )
        return

    # Show typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    # Get file details
    message = update.message
    if message.voice:
        file_id = message.voice.file_id
        file_name = "voice.ogg"
    elif message.audio:
        file_id = message.audio.file_id
        file_name = message.audio.file_name or "audio.mp3"
    elif message.video_note:
        file_id = message.video_note.file_id
        file_name = "video_note.mp4"
    else:
        await update.message.reply_text("Please send a voice note or audio file.")
        return

    # Send processing message
    processing_msg = await update.message.reply_text(
        "⏳ Processing your voice note...\n\n"
        "🔊 Transcribing → 📋 Summarizing → ✅ Extracting actions"
    )

    try:
        # Download audio
        audio_data, detected_name = await download_audio(context.bot, file_id)
        actual_name = detected_name or file_name

        # Run agent
        result = await process_audio(audio_data, actual_name)

        # Delete processing message
        await processing_msg.delete()

        if result.get("error"):
            await update.message.reply_text(
                f"❌ Something went wrong: {result['error']}\n\nPlease try again."
            )
            return

        # Format and send result
        formatted = format_result(result)
        await update.message.reply_text(formatted, parse_mode="Markdown")

        # Increment usage
        await increment_usage(user_id)

        # Show remaining usage for free users
        if not premium:
            new_usage = daily_usage + 1
            remaining = FREE_DAILY_LIMIT - new_usage
            if remaining > 0:
                await update.message.reply_text(
                    f"📊 *{remaining} free summar{'y' if remaining == 1 else 'ies'} remaining today.*",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "📊 *You've used all free summaries for today.* Resets at midnight UTC.",
                    parse_mode="Markdown",
                )

    except Exception as e:
        logger.error(f"Audio processing error: {e}")
        await processing_msg.delete()
        await update.message.reply_text(
            "❌ An error occurred while processing your audio. Please try again."
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎙 Send me a *voice note* or *audio file* to summarize!\n\n"
        "Use /help for more info.",
        parse_mode="Markdown",
    )


# ── Build Application ──────────────────────────────────────────────────────────

application = (
    Application.builder()
    .token(TELEGRAM_TOKEN)
    .build()
)

application.add_handler(CommandHandler("start", start_handler))
application.add_handler(CommandHandler("help", help_handler))
application.add_handler(CommandHandler("usage", usage_handler))
application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE, audio_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
