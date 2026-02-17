from celery import Task
from app.tasks.celery_app import celery_app
from app.config import get_settings
import asyncio

settings = get_settings()


class AsyncTask(Task):
    """Base task that runs async functions."""
    def __call__(self, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.run_async(*args, **kwargs))


@celery_app.task(base=AsyncTask, name="tasks.send_telegram_notification")
async def send_telegram_notification(chat_id: str, message: str) -> dict:
    """Send Telegram notification."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return {"status": "skipped", "reason": "Telegram not configured"}
    
    try:
        from telegram import Bot
        
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=chat_id, text=message)
        
        return {"status": "success", "chat_id": chat_id}
    
    except Exception as e:
        return {"status": "error", "error": str(e)}


@celery_app.task(base=AsyncTask, name="tasks.send_2fa_code")
async def send_2fa_code(chat_id: str, code: str) -> dict:
    """Send 2FA verification code via Telegram."""
    message = f"🔐 Your verification code is: {code}\n\nValid for 5 minutes."
    return await send_telegram_notification(chat_id, message)


@celery_app.task(base=AsyncTask, name="tasks.send_order_confirmation")
async def send_order_confirmation(
    chat_id: str,
    order_id: str,
    market_ticker: str,
    side: str,
    action: str,
    count: int,
    price: int,
) -> dict:
    """Send order confirmation notification."""
    message = (
        f"✅ Order placed successfully!\n\n"
        f"Order ID: {order_id}\n"
        f"Market: {market_ticker}\n"
        f"Action: {action.upper()} {count} {side.upper()} @ ${price/100:.2f}"
    )
    return await send_telegram_notification(chat_id, message)


@celery_app.task(base=AsyncTask, name="tasks.send_order_fill")
async def send_order_fill(
    chat_id: str,
    order_id: str,
    market_ticker: str,
    filled_count: int,
    fill_price: int,
) -> dict:
    """Send order fill notification."""
    message = (
        f"💰 Order filled!\n\n"
        f"Order ID: {order_id}\n"
        f"Market: {market_ticker}\n"
        f"Filled: {filled_count} contracts @ ${fill_price/100:.2f}"
    )
    return await send_telegram_notification(chat_id, message)


@celery_app.task(base=AsyncTask, name="tasks.send_position_alert")
async def send_position_alert(
    chat_id: str,
    market_ticker: str,
    alert_type: str,
    message_details: str,
) -> dict:
    """Send position alert (stop loss, take profit, etc)."""
    emoji = "⚠️" if alert_type == "stop_loss" else "🎯"
    message = (
        f"{emoji} Position Alert: {alert_type.replace('_', ' ').title()}\n\n"
        f"Market: {market_ticker}\n"
        f"{message_details}"
    )
    return await send_telegram_notification(chat_id, message)


@celery_app.task(base=AsyncTask, name="tasks.send_error_alert")
async def send_error_alert(chat_id: str, error_type: str, error_message: str) -> dict:
    """Send error alert notification."""
    message = (
        f"❌ Error Alert: {error_type}\n\n"
        f"{error_message}\n\n"
        f"Please check your trading terminal."
    )
    return await send_telegram_notification(chat_id, message)