"""Looking things up for AI chat, with DeepSeek's Search API (POST /v1/tools/search).

She can't know today's news, the weather or the latest version of anything. The judgement before
each reply (prompts/chat.yaml register) says when a message needs a search, what to search for and
how fresh the answer must be; the pages found ride along as evidence, with search_rules. Most
messages need none, so most replies cost nothing extra.

Searches are limited by date and language with the API's own operators: without after:, questions
like the weather come back as undated snapshots of old pages that contradict each other, and
without lang: half the results can be in French or Vietnamese.
"""
from datetime import timedelta
import aiohttp


URL = "https://api.deepseek.com/v1/tools/search"
TIMEOUT = 8
PER_DAY = 30            # searches a group can make a day (ai_chat.web_search_per_day)
RETRY_SECONDS = 600     # after a failure, replies go without a search for this long
RESULTS = 5
MIN_SCORE = 1.0         # the API scores relevance from 0 to 3, and calls 1.5 and up high quality
RESULT_CHARS = 300      # of each page's summary: plenty for a short group reply


class SearchError(Exception):
    pass


def query(words, days, today):
    """What to send: the words, limited to Chinese or English pages from the last `days` days."""
    return f"{words} lang:zh,en after:{(today - timedelta(days=days)).isoformat()}"


def lines(results):
    """The useful pages, one line each: title, site, date, what it says. No addresses, since QQ
    refuses messages with links in them, so she shouldn't have any to repeat."""
    found = []
    for result in results:
        text = " ".join(str(result.get("summary") or result.get("snippet") or "").split())[:RESULT_CHARS]
        if text and float(result.get("rerank_score") or 0) >= MIN_SCORE:
            source = " ".join(filter(None, [str(result.get("site_name") or ""), str(result.get("date") or "")[:10]]))
            found.append(f"- {result.get('title') or ''}（{source}）{text}")
    return found


class DeepSeekSearch:
    def __init__(self, api_key):
        self.api_key = api_key

    async def search(self, text):
        """The API's results; [] when nothing matched. Raises SearchError when it refused."""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
            async with session.post(URL, json={"query": text, "count": RESULTS},
                                    headers={"Authorization": f"Bearer {self.api_key}"}) as resp:
                if resp.status != 200:
                    raise SearchError(f"HTTP {resp.status}: {(await resp.text())[:200]}")
                return (await resp.json(content_type=None)).get("results") or []
