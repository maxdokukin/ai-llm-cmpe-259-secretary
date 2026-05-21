import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional


returns_data = True

tool_schema = {
    "type": "function",
    "function": {
        "name": "internet_search",
        "description": (
            "Searches the live internet without an API key using DuckDuckGo HTML results. "
            "Returns titles, URLs, snippets, and optional metadata when available. "
            "This uses an unofficial HTML endpoint, so it may be less reliable than a paid API."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up on the internet.",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of results to return. Must be between 1 and 10.",
                    "default": 5,
                },
                "freshness": {
                    "type": "string",
                    "description": (
                        "Optional freshness filter. Use 'pd' for past day, "
                        "'pw' for past week, 'pm' for past month, or 'py' for past year. "
                        "Support is best-effort because this is not an official API."
                    ),
                    "enum": ["pd", "pw", "pm", "py"],
                },
                "country": {
                    "type": "string",
                    "description": "Optional 2-letter country code, such as US, GB, DE.",
                    "default": "US",
                },
                "search_lang": {
                    "type": "string",
                    "description": "Optional search language, such as en, de, fr.",
                    "default": "en",
                },
            },
            "required": ["query"],
        },
    },
}


DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"


def _clamp_count(count: Any) -> int:
    try:
        value = int(count)
    except Exception:
        value = 5

    if value < 1:
        return 1

    if value > 10:
        return 10

    return value


def _clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _decode_duckduckgo_url(url: str) -> str:
    """
    DuckDuckGo often wraps result URLs like:
        /l/?uddg=https%3A%2F%2Fexample.com

    This extracts the real target URL.
    """
    url = _clean_text(url)

    if not url:
        return ""

    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    if "uddg" in query and query["uddg"]:
        return urllib.parse.unquote(query["uddg"][0])

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("/"):
        return urllib.parse.urljoin("https://duckduckgo.com", url)

    return url


def _freshness_to_duckduckgo(freshness: Optional[str]) -> Optional[str]:
    """
    DuckDuckGo's HTML endpoint accepts df values in some contexts:
        d = day
        w = week
        m = month
        y = year

    This is undocumented / unofficial.
    """
    if not freshness:
        return None

    freshness = _clean_text(freshness)

    mapping = {
        "pd": "d",
        "pw": "w",
        "pm": "m",
        "py": "y",
    }

    return mapping.get(freshness)


def _region_code(country: str, search_lang: str) -> str:
    """
    DuckDuckGo region examples:
        us-en
        uk-en
        de-de
        fr-fr

    This is best-effort.
    """
    country = _clean_text(country or "US").lower()
    lang = _clean_text(search_lang or "en").lower()

    if country == "gb":
        country = "uk"

    return f"{country}-{lang}"


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, Any]] = []

        self._capturing_title = False
        self._capturing_snippet = False

        self._title_parts: List[str] = []
        self._snippet_parts: List[str] = []

        self._current_url = ""
        self._last_result_index: Optional[int] = None

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")

        if tag == "a" and "result__a" in class_name:
            self._capturing_title = True
            self._title_parts = []
            self._current_url = _decode_duckduckgo_url(attrs_dict.get("href", ""))

        if tag in {"a", "div"} and "result__snippet" in class_name:
            self._capturing_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capturing_title:
            title = _clean_text(" ".join(self._title_parts))
            url = _clean_text(self._current_url)

            if title and url:
                self.results.append(
                    {
                        "title": title,
                        "url": url,
                        "description": "",
                    }
                )
                self._last_result_index = len(self.results) - 1

            self._capturing_title = False
            self._title_parts = []
            self._current_url = ""

        if tag in {"a", "div"} and self._capturing_snippet:
            snippet = _clean_text(" ".join(self._snippet_parts))

            if snippet and self._last_result_index is not None:
                if not self.results[self._last_result_index].get("description"):
                    self.results[self._last_result_index]["description"] = snippet

            self._capturing_snippet = False
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._capturing_title:
            self._title_parts.append(data)

        if self._capturing_snippet:
            self._snippet_parts.append(data)


def _extract_web_results(html: str, count: int) -> List[Dict[str, Any]]:
    parser = DuckDuckGoHTMLParser()
    parser.feed(html)

    results: List[Dict[str, Any]] = []

    seen_urls = set()

    for item in parser.results:
        title = _clean_text(item.get("title"))
        url = _clean_text(item.get("url"))
        description = _clean_text(item.get("description"))

        if not title or not url:
            continue

        if url in seen_urls:
            continue

        seen_urls.add(url)

        results.append(
            {
                "title": title,
                "url": url,
                "description": description,
            }
        )

        if len(results) >= count:
            break

    return results


def execute(
    query: str,
    count: int = 5,
    freshness: Optional[str] = None,
    country: str = "US",
    search_lang: str = "en",
) -> str:
    """
    Searches the live internet without an API key using DuckDuckGo HTML search.

    No environment variables required.

    Example:
        execute("latest AI agent tooling news", count=3, freshness="pm")
    """
    try:
        query = _clean_text(query)

        if not query:
            return "Error: query is required."

        count = _clamp_count(count)

        df = _freshness_to_duckduckgo(freshness)
        if freshness and not df:
            return "Error: freshness must be one of: pd, pw, pm, py."

        params = {
            "q": query,
            "kl": _region_code(country, search_lang),
        }

        if df:
            params["df"] = df

        body = urllib.parse.urlencode(params).encode("utf-8")

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0 Safari/537.36"
            ),
        }

        req = urllib.request.Request(
            DUCKDUCKGO_HTML_URL,
            data=body,
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=20) as response:
            html = response.read().decode("utf-8", errors="replace")

        results = _extract_web_results(html, count)

        output: Dict[str, Any] = {
            "query": query,
            "count": len(results),
            "results": results,
            "source": "duckduckgo_html",
        }

        if not results:
            output["warning"] = (
                "No results parsed. DuckDuckGo may have changed its HTML, "
                "blocked the request, or returned no results."
            )

        return json.dumps(output, ensure_ascii=False, indent=2)

    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""

        return (
            f"Error: DuckDuckGo HTTP error {e.code} - {e.reason}. "
            f"Response body: {body[:1000]}"
        )

    except urllib.error.URLError as e:
        return f"Error: Network error while searching internet: {e.reason}"

    except Exception as e:
        return f"Error executing internet search: {str(e)}"


if __name__ == "__main__":
    print(execute("latest AI agent tooling news", count=3, freshness="pm"))