import asyncio
import logging

logger = logging.getLogger(__name__)


async def fetch_with_retry(session, url: str, max_retries: int = 3, delay: int = 2):
    for attempt in range(max_retries):
        try:
            async with session.get(url, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
                logger.debug("HTTP %d for %s", r.status, url)
        except Exception as e:
            logger.debug("Attempt %d failed for %s: %s", attempt + 1, url, e)
        await asyncio.sleep(delay * (attempt + 1))
    return None
