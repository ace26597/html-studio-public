"""Instructions for a project (CONTRACT.md, "Instructions for a project")."""

from __future__ import annotations

import pytest

from studio2_server import instructions
from studio2_server.turns import compose_prompt


def test_the_text_is_tidied_and_kept_as_the_person_wrote_it():
    assert instructions.clean("  Always use Indian number format.\r\n\r\nEnd with the sources.  \n\n") == (
        "Always use Indian number format.\n\nEnd with the sources.")
    assert instructions.clean("a\x00b\x07c\td\x7f") == "abc\td"
    assert instructions.clean("line one   \nline two\t") == "line one\nline two"
    assert instructions.clean(None) == "" and instructions.clean("  \n ") == ""


@pytest.mark.parametrize("raw", [12, ["always"], {"text": "x"}, True])
def test_only_text_is_taken(raw):
    with pytest.raises(instructions.InstructionsError, match="plain text"):
        instructions.clean(raw)


def test_the_limit_counts_the_tidied_text():
    assert len(instructions.clean("x" * 4000 + "   \n")) == 4000
    with pytest.raises(instructions.InstructionsError, match="4,000 characters at most; this is 4,001"):
        instructions.clean("x" * 4001)


def test_the_fact_quotes_every_line_and_says_what_it_cannot_change():
    told = instructions.fact("Always use Indian number format.\n\nEnd with the sources.")
    assert "  | Always use Indian number format.\n  |\n  | End with the sources.\n" in told
    assert "it replaces any earlier version in this conversation" in told
    assert "the request comes first" in told and "never drop it quietly" in told
    assert "do not change what you may do" in told
    assert instructions.fact("") is None and instructions.fact(None) is None
    assert "no standing instructions for this project now" in instructions.fact("", cleared=True)


async def test_saving_reading_and_clearing(client, studio, project):
    pid = project["id"]
    assert project["instructions"] == ""
    assert "standing instructions" not in studio.agent_context(pid)

    saved = await client.patch(f"/api/projects/{pid}", json={"instructions": " Keep the tone plain.\r\nNo exclamation marks. "})
    assert saved.status_code == 200
    assert saved.json()["instructions"] == "Keep the tone plain.\nNo exclamation marks."
    assert (await client.get(f"/api/projects/{pid}")).json()["instructions"] == "Keep the tone plain.\nNo exclamation marks."
    told = studio.agent_context(pid)
    assert "  | Keep the tone plain.\n  | No exclamation marks.\n" in told
    # It reaches the agent inside the project facts of the request, with the request's own text first.
    prompt = compose_prompt("Make the report", None, [], studio.store.workspace(pid), context=told)
    assert prompt.startswith("Make the report\n\n<studio-project>") and "| Keep the tone plain." in prompt

    # Another change to the project leaves them alone.
    renamed = await client.patch(f"/api/projects/{pid}", json={"name": "Quarter report"})
    assert renamed.json()["instructions"] == "Keep the tone plain.\nNo exclamation marks."

    for empty in ("", None, "   \n"):
        await client.patch(f"/api/projects/{pid}", json={"instructions": "Something."})
        cleared = await client.patch(f"/api/projects/{pid}", json={"instructions": empty})
        assert cleared.status_code == 200 and cleared.json()["instructions"] == ""
        assert "instructions_cleared" not in cleared.json()
        told = studio.agent_context(pid)
        assert "no standing instructions for this project now" in told and "| Something." not in told
    # Cleared twice in a row still says so; text again takes the line away.
    await client.patch(f"/api/projects/{pid}", json={"instructions": ""})
    assert "no standing instructions for this project now" in studio.agent_context(pid)
    await client.patch(f"/api/projects/{pid}", json={"instructions": "Metric units."})
    told = studio.agent_context(pid)
    assert "| Metric units." in told and "no standing instructions" not in told


async def test_a_project_that_never_had_any_is_told_nothing(client, studio, project):
    pid = project["id"]
    await client.patch(f"/api/projects/{pid}", json={"instructions": ""})
    assert "standing instructions" not in studio.agent_context(pid)


async def test_what_is_refused(client, project):
    pid = project["id"]
    long = await client.patch(f"/api/projects/{pid}", json={"instructions": "x" * 4001})
    assert long.status_code == 400 and long.json()["error"]["code"] == "invalid_request"
    assert "this is 4,001" in long.json()["error"]["message"]
    wrong = await client.patch(f"/api/projects/{pid}", json={"instructions": ["always"]})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "invalid_request"
    assert (await client.get(f"/api/projects/{pid}")).json()["instructions"] == ""


async def test_each_project_has_its_own(client, studio, project):
    other = (await client.post("/api/projects", json={"name": "Other"})).json()
    await client.patch(f"/api/projects/{project['id']}", json={"instructions": "Only here."})
    assert other["instructions"] == ""
    assert "Only here." not in studio.agent_context(other["id"])
    assert "| Only here." in studio.agent_context(project["id"])


# -- for every project (CONTRACT.md, "Instructions for every project") ------------------------
async def test_one_text_reaches_every_project_before_the_projects_own(client, studio, project):
    pid = project["id"]
    assert (await client.get("/api/instructions")).json() == {"text": ""}
    assert "every project of theirs" not in studio.agent_context(pid, None)        # never written: nothing said
    saved = await client.put("/api/instructions", json={"text": "  My work is in Maharashtra, India. \r\nUse rupees.  "})
    assert saved.status_code == 200 and saved.json() == {"text": "My work is in Maharashtra, India.\nUse rupees."}
    assert (await client.get("/api/instructions")).json()["text"].startswith("My work is in Maharashtra")
    other = (await client.post("/api/projects", json={"name": "Another"})).json()["id"]
    for each in (pid, other):                                                      # every project, at once
        told = studio.agent_context(each, None)
        assert "standing instructions for every project of theirs" in told and "  | Use rupees." in told
        assert "do not ask what they already answer" in told and "approvals" in told
    await client.patch(f"/api/projects/{pid}", json={"instructions": "Here, use dollars."})
    told = studio.agent_context(pid, None)
    assert told.index("for every project of theirs") < told.index("standing instructions for this project")
    assert "this project's own instructions differ, those come first" in told
    # Cleared: each request says none applies now, so an earlier message's copy is not followed.
    assert (await client.put("/api/instructions", json={"text": ""})).json() == {"text": ""}
    assert "no standing instructions for every project now" in studio.agent_context(other, None)
    assert (studio.settings.data_dir / "instructions.json").is_file()


async def test_what_the_every_project_text_refuses(client, studio):
    for bad in ({"text": 5}, {"text": ["a"]}, {"text": "x" * 4001}, {}, {"words": "x"}):
        answer = await client.put("/api/instructions", json=bad)
        assert answer.status_code == 400 and answer.json()["error"]["code"] == "invalid_request", bad
    assert (await client.put("/api/instructions", json={"text": None})).json() == {"text": ""}
    # A file someone wrote by hand: read as nothing, never an error.
    (studio.settings.data_dir / "instructions.json").write_text("not json")
    assert (await client.get("/api/instructions")).json() == {"text": ""}


async def test_a_shared_studio_has_no_every_project_text(client, studio, project, settings):
    assert (await client.put("/api/instructions", json={"text": "Use rupees."})).status_code == 200
    settings.shared = True
    for answer in (await client.get("/api/instructions"), await client.put("/api/instructions", json={"text": "Mine now."})):
        assert answer.status_code == 403 and answer.json()["error"]["code"] == "not_for_shared"
    assert "every project of theirs" not in studio.agent_context(project["id"], None)     # the owner's text stays out
    settings.shared = False
    assert (await client.get("/api/instructions")).json() == {"text": "Use rupees."}      # and was not changed


def test_the_every_project_text_is_kept_and_cleared(tmp_path):
    from studio2_server.instructions import Everywhere, fact_everywhere
    kept = Everywhere(tmp_path / "instructions.json")
    assert kept.read() == {"text": "", "cleared": False} and kept.fact() is None
    assert kept.write("Use rupees.") == "Use rupees." and "  | Use rupees." in kept.fact()
    assert kept.write("") == "" and kept.read() == {"text": "", "cleared": True}
    assert "no longer apply" in kept.fact() and fact_everywhere("", False) is None
