import hashlib
import json
from pathlib import Path

from continuous_audit_lab.data.download import CKAN_URL, download_all

BASE_URL = "https://www.southlakeland.gov.uk/media/7221/"
BODIES = {
    "a-march-2020.csv": b"txn_date,amount\n2020-03-01,10\n",
    "b-april-2020.csv": b"txn_date,amount\n2020-04-01,20\n",
}


class FakeResponse:
    def __init__(
        self, body: bytes = b"", json_data: dict | None = None, headers: dict | None = None
    ):
        self._body = body
        self._json_data = json_data
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._json_data

    def iter_content(self, chunk_size: int = 1) -> list[bytes]:
        return [self._body]


class FakeSession:
    def __init__(self) -> None:
        self.gets: list[str] = []
        self.heads: list[str] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.gets.append(url)
        if url == CKAN_URL:
            resources = [{"name": name, "url": BASE_URL + name} for name in BODIES]
            return FakeResponse(json_data={"result": {"resources": resources}})
        return FakeResponse(body=BODIES[url.removeprefix(BASE_URL)])

    def head(self, url: str, **kwargs: object) -> FakeResponse:
        self.heads.append(url)
        size = len(BODIES[url.removeprefix(BASE_URL)])
        return FakeResponse(headers={"Content-Length": str(size)})


def run(tmp_path: Path, session: FakeSession, dry_run: bool = False) -> None:
    download_all(
        dest_dir=str(tmp_path / "raw"),
        manifest_path=str(tmp_path / "raw" / "checksums.json"),
        dry_run=dry_run,
        session=session,
    )


def test_second_run_downloads_nothing(tmp_path: Path) -> None:
    run(tmp_path, FakeSession())
    second = FakeSession()
    run(tmp_path, second)
    assert second.gets == [CKAN_URL]


def test_manifest_checksums_match_downloaded_files(tmp_path: Path) -> None:
    run(tmp_path, FakeSession())
    manifest = json.loads((tmp_path / "raw" / "checksums.json").read_text(encoding="utf-8"))
    for name, body in BODIES.items():
        entry = manifest[f"7221_{name}"]
        assert entry["sha256"] == hashlib.sha256(body).hexdigest()
        assert entry["bytes"] == len(body)
        assert entry["url"] == BASE_URL + name


def test_dry_run_downloads_nothing(tmp_path: Path) -> None:
    session = FakeSession()
    run(tmp_path, session, dry_run=True)
    assert session.gets == [CKAN_URL]
    assert session.heads == [BASE_URL + name for name in BODIES]
    assert not (tmp_path / "raw").exists()
