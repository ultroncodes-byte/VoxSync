import os
from datetime import date
from supabase import create_client, Client
import logging

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def create_user(user_id: int, name: str, username: str = None):
    """Create user if they don't exist."""
    try:
        existing = supabase.table("users").select("*").eq("telegram_id", user_id).execute()
        if not existing.data:
            supabase.table("users").insert({
                "telegram_id": user_id,
                "name": name,
                "username": username or "",
                "is_premium": False,
                "daily_usage": 0,
                "usage_date": str(date.today()),
                "total_summaries": 0,
            }).execute()
            logger.info(f"Created user {user_id}")
    except Exception as e:
        logger.error(f"create_user error: {e}")


async def get_user(user_id: int) -> dict | None:
    """Get user record, resetting daily usage if it's a new day."""
    try:
        result = supabase.table("users").select("*").eq("telegram_id", user_id).execute()
        if not result.data:
            return None

        user = result.data[0]
        today = str(date.today())

        # Reset daily usage if it's a new day
        if user.get("usage_date") != today:
            supabase.table("users").update({
                "daily_usage": 0,
                "usage_date": today,
            }).eq("telegram_id", user_id).execute()
            user["daily_usage"] = 0
            user["usage_date"] = today

        return user

    except Exception as e:
        logger.error(f"get_user error: {e}")
        return None


async def increment_usage(user_id: int):
    """Increment daily usage and total summaries."""
    try:
        user = await get_user(user_id)
        if user:
            supabase.table("users").update({
                "daily_usage": user.get("daily_usage", 0) + 1,
                "total_summaries": user.get("total_summaries", 0) + 1,
                "usage_date": str(date.today()),
            }).eq("telegram_id", user_id).execute()
    except Exception as e:
        logger.error(f"increment_usage error: {e}")


async def is_premium(user_id: int) -> bool:
    """Check if user is premium."""
    try:
        result = supabase.table("users").select("is_premium").eq("telegram_id", user_id).execute()
        if result.data:
            return result.data[0].get("is_premium", False)
        return False
    except Exception as e:
        logger.error(f"is_premium error: {e}")
        return False


async def set_premium(user_id: int, status: bool = True):
    """Set user premium status."""
    try:
        supabase.table("users").update({
            "is_premium": status
        }).eq("telegram_id", user_id).execute()
        logger.info(f"Set user {user_id} premium={status}")
    except Exception as e:
        logger.error(f"set_premium error: {e}")
