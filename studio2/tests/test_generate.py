"""Generated media: priced, approved by the person every time, recorded (CONTRACT.md, "Generated media")."""

import json
import stat
import sys

import pytest

from studio2_server import generate, tools

FAKE_CLI = '''#!%s
import json, os, sys
state_path = os.environ["FAKE_HF_STATE"]
state = json.load(open(state_path))
args = [a for a in sys.argv[1:] if a not in ("--json", "--no-color")]
state["calls"].append(args)
def done(obj=None, code=0, err=""):
    json.dump(state, open(state_path, "w"))
    if obj is not None: print(json.dumps(obj))
    if err: print(err, file=sys.stderr)
    sys.exit(code)
if args[:2] == ["account", "status"]: done({"credits": state["credits"]})
if args[:2] == ["model", "list"]: done([{"job_type": "gpt_image_2", "display_name": "GPT Image 2", "type": "image"},
                                        {"job_type": "seedance_2_0", "display_name": "Seedance 2.0", "type": "video"}])
if args[:2] == ["model", "get"]: done({"job_type": args[2], "display_name": "X", "type": "image",
                                       "params": [{"name": "prompt", "type": "string", "required": True}]})
if args[:2] == ["generate", "cost"]:
    if "--bogus" in args: done(code=1, err="Error: Unknown params: bogus Hint: Run: hf model get gpt_image_2")
    done({"credits": state["price"]})
if args[:2] == ["generate", "create"]:
    if state["mode"] == "fault": done(code=1, err="Error: request failed: HTTP 500 Internal Server Error")
    if state["mode"] == "refused": done([{"id": "j2", "status": "nsfw"}])
    state["credits"] = round(state["credits"] - state["price"], 2)
    done([{"id": "j1", "status": "completed", "result_url": "https://cdn.example/out.png", "params": {"width": 1344, "height": 752}}])
done(code=2, err="unknown command")
'''


@pytest.fixture
def service(tmp_path, monkeypatch, studio):
    state = tmp_path / "hf-state.json"
    state.write_text(json.dumps({"credits": 50.0, "price": 6.5, "mode": "ok", "calls": []}))
    cli = tmp_path / "higgsfield"
    cli.write_text(FAKE_CLI % sys.executable)
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STUDIO2_HIGGSFIELD", str(cli))
    monkeypatch.setenv("FAKE_HF_STATE", str(state))

    async def download(url):
        import io

        from PIL import Image
        data = io.BytesIO()
        Image.new("RGB", (64, 36), "navy").save(data, "PNG")
        return data.getvalue()
    monkeypatch.setattr(generate.Generator, "_download", staticmethod(download))

    class Service:
        def read(self):
            return json.loads(state.read_text())

        def set(self, **changes):
            state.write_text(json.dumps({**self.read(), **changes}))

        def created(self):
            return [call for call in self.read()["calls"] if call[:2] == ["generate", "create"]]
    return Service()


def answering(studio, project, answer: bool) -> list:
    asked = []

    async def approve(tool, title, detail, reason, always=False):
        asked.append({"tool": tool, "title": title, "detail": detail, "reason": reason, "always": always})
        return answer
    studio.approvers[project["id"]] = approve
    return asked


async def call(studio, project, **args):
    result = await tools.call(studio, project["id"], "generate_media", args)
    text = result["content"][0]["text"]
    return (None, text) if result.get("is_error") else (json.loads(text), None)


def test_settings_become_command_line_flags():
    assert generate.flags({"prompt": "a b", "generate_audio": False, "duration": 5, "skip": None}) == [
        "--prompt", "a b", "--generate_audio", "false", "--duration", "5"]
    with pytest.raises(generate.GenerateError):
        generate.flags({"--wait; rm": 1})
    assert generate.is_server_fault("Error: request failed: HTTP 500 Internal Server Error")
    assert not generate.is_server_fault("Error: prompt was refused (nsfw)")


async def test_nothing_is_spent_without_the_persons_yes(studio, project, service):
    made, error = await call(studio, project, kind="image", out="media/hero.png", prompt="a roof")
    assert made is None and "did not allow" in error and service.created() == []       # no turn: nobody to ask
    asked = answering(studio, project, False)
    made, error = await call(studio, project, kind="image", out="media/hero.png", prompt="a roof")
    assert made is None and "Nothing was spent" in error and service.created() == []
    assert asked[0]["always"] and "6.5 credits" in asked[0]["title"] and "6.5 of your 50" in asked[0]["reason"]
    assert "a roof" in asked[0]["detail"] and "media/hero.png" in asked[0]["detail"]


async def test_an_approved_picture_lands_in_the_project_and_is_recorded(studio, project, service, client):
    await client.put(f"/api/projects/{project['id']}", json={"approvals": "allow"})
    asked = answering(studio, project, True)
    made, error = await call(studio, project, kind="image", out="media/hero.jpg", prompt="a roof at dusk",
                             aspect_ratio="16:9", quality="medium")
    assert error is None and len(asked) == 1 and asked[0]["always"]      # "allow" projects are asked as well
    assert made["file"] == "media/hero.jpg" and made["credits_spent"] == 6.5 and made["credits_left"] == 43.5
    assert (studio.store.workspace(project["id"]) / "media" / "hero.jpg").read_bytes()[:2] == b"\xff\xd8"
    create = service.created()[0]
    assert create[2] == "gpt_image_2" and create[create.index("--aspect_ratio") + 1] == "16:9" and "--wait" in create
    row = json.loads(studio.generator.usage_file.read_text().splitlines()[-1])
    assert row["credits"] == 6.5 and row["project"] == project["id"] and row["outcome"] == "completed"
    assert (await client.get("/api/generation")).json()["spent_today"] == 6.5


async def test_a_price_over_the_balance_or_the_daily_limit_is_refused_before_asking(studio, project, service):
    asked = answering(studio, project, True)
    service.set(price=80.0)
    assert "account has 50" in (await call(studio, project, kind="image", out="media/a.png", prompt="x"))[1]
    service.set(price=30.0, credits=500.0)
    studio.generator.record(project="other", credits=75.0)
    assert "limit is 100" in (await call(studio, project, kind="image", out="media/a.png", prompt="x"))[1]
    assert "Unknown params" in (await call(studio, project, kind="image", out="media/a.png", prompt="x", params={"bogus": 1}))[1]
    assert "model" in (await call(studio, project, kind="audio", out="media/a.mp3", prompt="x"))[1]
    assert "end in" in (await call(studio, project, kind="image", out="media/a.mp4", prompt="x"))[1]
    assert asked == [] and service.created() == []


async def test_a_server_fault_pauses_generation_and_a_refusal_does_not(studio, project, service, client):
    answering(studio, project, True)
    service.set(mode="refused")
    assert "Do not retry with another model" in (await call(studio, project, kind="image", out="media/a.png", prompt="x"))[1]
    assert studio.generator.halted() is None
    service.set(mode="fault")
    assert "paused until" in (await call(studio, project, kind="image", out="media/a.png", prompt="x"))[1]
    service.set(mode="ok")
    before = len(service.created())
    assert "paused until" in (await call(studio, project, kind="image", out="media/a.png", prompt="x"))[1]
    assert len(service.created()) == before                               # not even attempted
    assert (await client.get("/api/generation")).json()["paused"]
    assert (await client.delete("/api/generation/pause")).status_code == 204
    assert (await call(studio, project, kind="image", out="media/a.png", prompt="x"))[0]["file"] == "media/a.png"


async def test_models_and_credits_can_be_looked_up_for_free(studio, project, service):
    result = await tools.call(studio, project["id"], "generation_models", {"kind": "image"})
    listed = json.loads(result["content"][0]["text"])
    assert [m["model"] for m in listed["models"]] == ["gpt_image_2"] and listed["credits_left"] == 50.0
    assert listed["defaults"] == {"image": "gpt_image_2", "video": "seedance_2_0"} and service.created() == []
