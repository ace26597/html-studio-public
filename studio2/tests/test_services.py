"""Keys for other services, and the order of web search (CONTRACT.md): the service's own variable
wins, a key is listed and never shown, Tavily answers first when it has a key and the public pages
answer when it cannot. No network: Tavily and the public pages are stand-ins."""

import httpx
import pytest

from studio2_server import cli, search, services

KEY = "tvly-test-0123456789"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    search._cache.clear()
    search._resting.clear()


def test_the_services_own_variable_wins_and_a_key_is_never_listed(settings):
    assert services.listing(settings, {}) == [{
        "name": "tavily", "label": "Tavily", "used_for": services.SERVICES["tavily"]["used_for"],
        "env": "TAVILY_API_KEY", "site": "https://tavily.com", "configured": False, "source": None}]
    services.save(settings, "tavily", f"  {KEY}  ")
    assert services.key(settings, "tavily", {}) == KEY and services.source(settings, "tavily", {}) == "store"
    assert services.key(settings, "tavily", {"TAVILY_API_KEY": "tvly-from-env"}) == "tvly-from-env"
    assert services.source(settings, "tavily", {"TAVILY_API_KEY": "tvly-from-env"}) == "env"
    assert KEY not in str(services.listing(settings, {}))
    assert not settings.config_path.exists() or KEY not in settings.config_path.read_text()      # never in the config
    services.remove(settings, "tavily")
    assert services.key(settings, "tavily", {}) is None
    with pytest.raises(ValueError):
        services.save(settings, "tavily", "   ")
    for call in (lambda: services.save(settings, "nope", "x"), lambda: services.remove(settings, "nope"),
                 lambda: services.key(settings, "nope")):
        with pytest.raises(services.UnknownService):
            call()


async def test_the_routes_save_list_and_remove_without_showing_the_key(client):
    saved = await client.put("/api/services/tavily", json={"key": KEY})
    assert saved.status_code == 200 and saved.json()["source"] == "store" and KEY not in saved.text
    listed = await client.get("/api/services")
    assert listed.json()[0]["configured"] is True and KEY not in listed.text
    assert (await client.put("/api/services/tavily", json={"key": " "})).json()["error"]["code"] == "invalid_key"
    assert (await client.put("/api/services/nope", json={"key": "x"})).status_code == 404
    removed = await client.delete("/api/services/tavily")
    assert removed.json()["configured"] is False and (await client.delete("/api/services/nope")).status_code == 404


def tavily(monkeypatch, status=200, body=None, seen=None):
    """Tavily and the public pages as stand-ins: what Tavily was sent is kept in `seen`."""
    async def post(self, url, headers=None, json=None, **_):
        (seen if seen is not None else []).append({"url": url, "headers": headers, "json": json})
        return httpx.Response(status, json=body if body is not None else {"results": []}, request=httpx.Request("POST", url))

    async def public(service, query, limit, retry):
        return [{"title": "From the public pages", "url": "https://example.org/p", "snippet": "x"}]

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(search, "_try", public)


async def test_tavily_answers_first_when_there_is_a_key(monkeypatch):
    seen = []
    tavily(monkeypatch, body={"results": [
        {"title": "Heat pumps", "url": "https://example.org/a", "content": "A  long\nsummary.", "score": 0.9},
        {"title": "No address", "content": "dropped"}, "junk"]}, seen=seen)
    found = await search.search("heat pump cost", 5, tavily_key=KEY)
    assert found["via"] == "tavily" and "note" not in found
    assert found["results"] == [{"title": "Heat pumps", "url": "https://example.org/a", "snippet": "A long summary."}]
    assert seen == [{"url": "https://api.tavily.com/search", "headers": {"Authorization": f"Bearer {KEY}"},
                     "json": {"query": "heat pump cost", "max_results": 5}}]
    assert (await search.search("heat pump cost", 5, tavily_key=KEY))["cached"] is True and len(seen) == 1
    without = await search.search("another question", 5)             # no key: the public pages, as before
    assert without["via"] in search.SERVICES and len(seen) == 1


@pytest.mark.parametrize("status, why, rests", [(401, "refused the key", True), (432, "limit is used up", True),
                                                (433, "limit is used up", True), (500, "HTTP 500", False)])
async def test_when_tavily_cannot_answer_the_public_pages_do_and_say_why(monkeypatch, status, why, rests):
    seen = []
    tavily(monkeypatch, status=status, seen=seen)
    found = await search.search("first question", 5, tavily_key=KEY)
    assert found["via"] in search.SERVICES and found["results"][0]["title"] == "From the public pages"
    assert "Tavily was asked first" in found["note"] and why in found["note"] and KEY not in found["note"]
    again = await search.search("second question", 5, tavily_key=KEY)
    assert len(seen) == (1 if rests else 2)                           # a bad key costs one request, not one per search
    assert ("left alone" in again["note"]) is rests


def test_a_search_connector_is_named_as_the_first_way_to_search(studio, project):
    assert services.search_connectors(["tavily-mcp", "Brave Search", "github", "postgres"]) == ["tavily-mcp", "Brave Search"]
    picked = {"skills": [], "connectors": [{"id": "c1", "name": "tavily-mcp", "how": "always"}, {"id": "c2", "name": "github", "how": "always"}]}
    context = studio.agent_context(project["id"], picked)
    assert "use the tools of the connector “tavily-mcp” first" in context and "“github”" not in context.split("For web search")[1].split("\n")[0]
    assert "For web search, use the tools of the connector" not in studio.agent_context(project["id"])


def test_the_command_lists_saves_and_removes_without_printing_the_key(settings, monkeypatch, capsys):
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls, *a, **k: settings))
    args = cli.build_parser().parse_args(["keys"])
    assert args.run(args) == 0 and "not set" in capsys.readouterr().out
    args = cli.build_parser().parse_args(["keys", "set", "tavily"])
    assert cli.cmd_keys(args, secret=lambda prompt: KEY) == 0
    args = cli.build_parser().parse_args(["keys", "list"])
    assert args.run(args) == 0
    out = capsys.readouterr().out
    assert "set, kept in" in out and KEY not in out
    args = cli.build_parser().parse_args(["keys", "remove", "tavily"])
    assert args.run(args) == 0 and services.key(settings, "tavily", {}) is None
    args = cli.build_parser().parse_args(["keys", "set", "nope"])
    assert cli.cmd_keys(args, secret=lambda prompt: "x") == 2


async def test_a_page_built_by_scripts_is_read_in_a_browser_without_being_asked(studio, project, monkeypatch):
    from studio2_server import tools

    async def empty_download(url):
        return (b"<html><body><div id='root'></div><script src='app.js'></script></body></html>", "text/html", url)

    class Browser:
        asked = []

        def reason(self):
            return None

        async def close(self):
            return None

        async def rendered(self, url):
            self.asked.append(url)
            return url, "<html><title>Built</title><body><p>The page says the price is 42 dollars today.</p></body></html>"

    monkeypatch.setattr(tools, "download", empty_download)
    monkeypatch.setattr(studio, "looker", Browser())
    answer = await tools.call(studio, project["id"], "fetch_source", {"url": "https://example.org/app"})
    assert "is_error" not in answer, answer
    view = __import__("json").loads(answer["content"][0]["text"])
    assert view["rendered"] is True and "42 dollars" in view["text_start"] and Browser.asked == ["https://example.org/app"]

    class NoBrowser(Browser):
        def reason(self):
            return "no browser here"

    monkeypatch.setattr(studio, "looker", NoBrowser())
    refused = await tools.call(studio, project["id"], "fetch_source", {"url": "https://example.org/other"})
    assert refused.get("is_error") and "render: true" in refused["content"][0]["text"]      # the old error stands
