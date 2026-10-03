import json

from podbay import config


def test_home_repo_comes_from_the_file_unless_the_environment_says_otherwise(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.delenv("PODBAY_HOME_REPO", raising=False)
    assert config.home_repo(path) == ""  # no file

    assert config.set_value("home-repo", "jeeves", path) == {"home_repo": "jeeves"}
    assert json.loads(path.read_text()) == {"home_repo": "jeeves"}
    assert config.home_repo(path) == "jeeves"

    monkeypatch.setenv("PODBAY_HOME_REPO", "other")
    assert config.home_repo(path) == "other"
    monkeypatch.setenv("PODBAY_HOME_REPO", "")
    assert config.home_repo(path) == ""  # set but empty: no home repo this run

    monkeypatch.delenv("PODBAY_HOME_REPO")
    config.set_value("home-repo", "", path)
    assert config.home_repo(path) == ""
    assert json.loads(path.read_text()) == {}


def test_unreadable_file_is_empty(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    assert config.read(path) == {}
    assert config.home_repo(path) == ""


def test_cmd_config_prints_and_sets(tmp_path, monkeypatch, capsys):
    from podbay.app import cmd_config

    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    cmd_config(None, None)
    assert capsys.readouterr().out == "head-jeeves = \nhead-jeeves-account = \nhome-repo = \nreview-model = \nvoice = \n"
    cmd_config("home-repo", "jeeves")
    assert "home-repo = jeeves" in capsys.readouterr().out
    cmd_config("home-repo", None)
    assert capsys.readouterr().out == "jeeves\n"
