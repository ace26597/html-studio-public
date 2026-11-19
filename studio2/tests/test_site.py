"""The guide and the benchmarks, served by the studio (CONTRACT.md, same heading). Zero model calls."""


def make_site(settings):
    root = settings.site_dir
    (root / "benchmarks" / "model-comparison" / "shots").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "index.html").write_text("<title>guide</title>")
    (root / "site.css").write_text("body { margin: 0 }")
    (root / "benchmarks" / "index.html").write_text("<title>benchmarks</title>")
    (root / "benchmarks" / "model-comparison" / "index.html").write_text("<title>comparison</title>")
    (root / "benchmarks" / "model-comparison" / "shots" / "one.jpg").write_bytes(b"\xff\xd8picture")
    (root / "studio2-plan.md").write_text("working notes, not for readers")
    (root / ".nojekyll").write_text("")
    (root / "notes.txt").write_text("also not a page")
    return root


async def test_without_the_folder_the_studio_says_so_and_serves_nothing(client):
    assert (await client.get("/api/health")).json()["site"] is False
    for path in ("/site/", "/site/index.html", "/site/benchmarks/"):
        answer = await client.get(path)
        assert answer.status_code == 404 and answer.json()["error"]["code"] == "not_found", path


async def test_the_pages_and_what_they_show_are_served_by_their_own_addresses(client, settings):
    make_site(settings)
    assert (await client.get("/api/health")).json()["site"] is True
    for path, title in (("/site/", "guide"), ("/site/index.html", "guide"), ("/site/benchmarks/", "benchmarks"),
                        ("/site/benchmarks/index.html", "benchmarks"),
                        ("/site/benchmarks/model-comparison/", "comparison")):
        answer = await client.get(path)
        assert answer.status_code == 200 and f"<title>{title}</title>" in answer.text, path
        assert answer.headers["content-type"].startswith("text/html") and answer.headers["cache-control"] == "no-cache"
    style = await client.get("/site/site.css")
    assert style.status_code == 200 and style.headers["content-type"].startswith("text/css")
    picture = await client.get("/site/benchmarks/model-comparison/shots/one.jpg")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/jpeg"


async def test_a_folder_named_without_its_slash_is_sent_to_the_address_with_it(client, settings):
    make_site(settings)
    for path, to in (("/site", "/site/"), ("/site/benchmarks", "/site/benchmarks/"),
                     ("/site/benchmarks/model-comparison", "/site/benchmarks/model-comparison/")):
        answer = await client.get(path, follow_redirects=False)
        assert answer.status_code == 307 and answer.headers["location"] == to, path


async def test_notes_hidden_files_other_kinds_and_paths_out_of_the_folder_are_not_served(client, settings, tmp_path):
    make_site(settings)
    (tmp_path / "secret.html").write_text("outside the folder")
    for path in ("/site/studio2-plan.md", "/site/.nojekyll", "/site/notes.txt", "/site/missing.html",
                 "/site/%2e%2e/secret.html", "/site/benchmarks/%2e%2e/%2e%2e/secret.html", "/site/..%2fsecret.html",
                 "/site/assets/", "/site/benchmarks/model-comparison/shots/"):
        answer = await client.get(path, follow_redirects=False)
        assert answer.status_code == 404, (path, answer.status_code)
        assert "working notes" not in answer.text and "outside the folder" not in answer.text
    # A client that tidies ".." away before sending asks for another address altogether; whatever
    # answers there, it is never the file outside the folder.
    for path in ("/site/../secret.html", "/site/benchmarks/../../secret.html"):
        assert "outside the folder" not in (await client.get(path, follow_redirects=False)).text


async def test_the_interface_still_answers_for_everything_else(client, settings):
    make_site(settings)
    settings.web_dist.mkdir()
    (settings.web_dist / "index.html").write_text("<title>ui</title>")
    assert "<title>ui</title>" in (await client.get("/")).text
    assert "<title>ui</title>" in (await client.get("/sites")).text          # not the guide's address
