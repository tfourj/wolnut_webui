import threading

import yaml

from wolnut.config_store import ConfigStore


def test_config_store_writes_atomically_and_preserves_mode(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("clients: []\n")
    path.chmod(0o640)
    store = ConfigStore(path)

    store.write({"nut": {"ups": "ups@localhost"}, "clients": []})

    assert yaml.safe_load(path.read_text())["nut"]["ups"] == "ups@localhost"
    assert path.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".config.yaml.*"))


def test_config_store_serializes_updates(tmp_path):
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    store.write({"count": 0})

    def increment():
        store.update(lambda value: {"count": value["count"] + 1})

    threads = [threading.Thread(target=increment) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.read() == {"count": 20}
