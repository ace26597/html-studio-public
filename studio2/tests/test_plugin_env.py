"""A plugin whose folder is inside the studio's data or a project folder (where the agent writes)
has its `${NAME}` filled from what the agent may see, never from the studio's own variables."""

from __future__ import annotations


async def test_a_plugin_kept_inside_the_studios_data_or_a_project_gets_no_studio_variable(client, studio, settings,
                                                                                          monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_SIDE_API_KEY", "sk-studio-only-0123")
    inside = settings.data_dir / "projects" / "p_x" / "workspace" / "plug"
    elsewhere = tmp_path / "real-plugins" / "plug"
    config = {"url": "http://127.0.0.1:19599/${STUDIO_SIDE_API_KEY}", "headers": {"X": "${STUDIO_SIDE_API_KEY}"}}
    monkeypatch.setattr(studio.lib.catalog, "ensure", lambda force=False: {})
    studio.lib.catalog.plugin_connectors = [
        {"plugin": "planted@x", "name": "planted", "root": str(inside), "config": config, "enabled": True},
        {"plugin": "real@x", "name": "real", "root": str(elsewhere), "config": config, "enabled": True}]
    assert "sk-studio-only-0123" not in str(studio.lib.server_config("plugin:planted@x:planted"))
    # A plugin installed in its usual place keeps reading the studio's environment, as before.
    assert "sk-studio-only-0123" in studio.lib.server_config("plugin:real@x:real")["url"]
    # A project edited in place (the person's own folder) counts as a project folder too.
    folder = tmp_path / "my-app"
    monkeypatch.setattr(studio.store, "in_place_folders", lambda: {folder.resolve()})
    studio.lib.catalog.plugin_connectors[1]["root"] = str(folder / "plugins" / "p")
    assert "sk-studio-only-0123" not in str(studio.lib.server_config("plugin:real@x:real"))
