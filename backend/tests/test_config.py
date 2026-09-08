from app import config


def test_update_env_file_preserves_unrelated_values(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_PORT=8000\nBITGET_API_KEY=old\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", env_path)

    config.update_env_file({"BITGET_API_KEY": "new", "BITGET_API_SECRET": "secret"})

    assert env_path.read_text(encoding="utf-8") == (
        "APP_PORT=8000\nBITGET_API_KEY=new\nBITGET_API_SECRET=secret\n"
    )
