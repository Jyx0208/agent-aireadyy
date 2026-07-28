from __future__ import annotations

import httpx
import pytest

from agent.pride.client import (
    PrideClient,
    PrideInvalidResponseError,
    search_projects_paginated,
)


def test_paginated_resume_fails_closed_when_legacy_client_has_no_page_cursor() -> None:
    class LegacyClient:
        def search_projects(self, _keyword: str, page_size: int = 100):
            return [{"accession": "PXD000001"}][:page_size]

    with pytest.raises(RuntimeError, match="does not support resumable page cursors"):
        search_projects_paginated(
            LegacyClient(),
            "immunopeptidomics",
            page_size=100,
            max_pages=2,
            max_results=150,
            start_page=1,
        )


def test_paginated_legacy_client_internal_type_error_is_not_retried() -> None:
    class BrokenClient:
        def search_projects(
            self,
            _keyword: str,
            page_size: int = 100,
            *,
            page: int = 0,
        ):
            del page_size, page
            raise TypeError("internal decoding failure")

    with pytest.raises(TypeError, match="internal decoding failure"):
        search_projects_paginated(
            BrokenClient(),
            "immunopeptidomics",
            page_size=100,
            max_pages=2,
            max_results=150,
        )


class _FakeResponse:
    def __init__(self, payload, *, text: str = "", content: bytes = b""):
        self._payload = payload
        self.text = text
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeHTTPClient:
    def __init__(self, pages: dict[int, object]):
        self.pages = pages
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        self.calls.append((path, params))
        page = params.get("page", 0)
        return _FakeResponse(self.pages.get(page, []))


def test_list_project_files_paginates_until_last_page():
    fake_client = _FakeHTTPClient(
        {
            0: [{"fileName": "page0-a.raw"}, {"fileName": "page0-b.raw"}],
            1: [{"fileName": "page1-a.raw"}],
        }
    )
    client = PrideClient()
    client._client = fake_client

    files = client.list_project_files("PXD000001", page_size=2)

    assert [item["fileName"] for item in files] == [
        "page0-a.raw",
        "page0-b.raw",
        "page1-a.raw",
    ]
    assert fake_client.calls == [
        ("/projects/PXD000001/files", {"pageSize": 2, "page": 0}),
        ("/projects/PXD000001/files", {"pageSize": 2, "page": 1}),
    ]


def test_search_projects_paginates_when_caller_requests_broad_coverage():
    fake_client = _FakeHTTPClient(
        {
            0: [{"accession": f"PXD0{index:05d}"} for index in range(100)],
            1: [{"accession": f"PXD1{index:05d}"} for index in range(50)],
        }
    )
    client = PrideClient()
    client._client = fake_client

    projects = client.search_projects("human", page_size=100, max_pages=5)

    assert len(projects) == 150
    assert fake_client.calls == [
        ("/search/projects", {"keyword": "human", "pageSize": 100, "page": 0}),
        ("/search/projects", {"keyword": "human", "pageSize": 100, "page": 1}),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "repository under maintenance"},
        [{"error": "repository under maintenance"}],
        ["repository under maintenance"],
    ],
)
def test_search_projects_rejects_http_success_payloads_that_are_not_project_lists(
    payload: object,
):
    client = PrideClient()
    client._client = _FakeHTTPClient({0: payload})

    with pytest.raises(PrideInvalidResponseError, match="invalid PRIDE response"):
        client.search_projects("human")


def test_search_projects_preserves_true_empty_list_as_zero_results():
    client = PrideClient()
    client._client = _FakeHTTPClient({0: []})

    assert client.search_projects("no-scientific-match") == []


def test_get_project_rejects_http_success_non_project_payload():
    client = PrideClient()
    client._client = _FakeHTTPClient(
        {0: {"error": "repository under maintenance"}}
    )

    with pytest.raises(PrideInvalidResponseError, match="invalid PRIDE response"):
        client.get_project("PXD000001")


def test_list_project_files_rejects_http_success_non_list_payload():
    client = PrideClient()
    client._client = _FakeHTTPClient(
        {0: {"error": "repository under maintenance"}}
    )

    with pytest.raises(PrideInvalidResponseError, match="invalid PRIDE response"):
        client.list_project_files("PXD000001")


def test_list_project_files_honors_pride_page_cap_of_100():
    fake_client = _FakeHTTPClient(
        {
            0: [{"fileName": f"page0-{index}.raw"} for index in range(100)],
            1: [{"fileName": f"page1-{index}.raw"} for index in range(100)],
            2: [{"fileName": "page2-0.raw"}],
        }
    )
    client = PrideClient()
    client._client = fake_client

    files = client.list_project_files("PXD000002", page_size=1000)

    assert len(files) == 201
    assert fake_client.calls == [
        ("/projects/PXD000002/files", {"pageSize": 100, "page": 0}),
        ("/projects/PXD000002/files", {"pageSize": 100, "page": 1}),
        ("/projects/PXD000002/files", {"pageSize": 100, "page": 2}),
    ]


def test_list_project_files_can_stop_after_max_files():
    fake_client = _FakeHTTPClient(
        {
            0: [{"fileName": f"page0-{index}.raw"} for index in range(100)],
            1: [{"fileName": f"page1-{index}.raw"} for index in range(100)],
            2: [{"fileName": "page2-0.raw"}],
        }
    )
    client = PrideClient()
    client._client = fake_client

    files = client.list_project_files("PXD000003", page_size=100, max_files=120)

    assert len(files) == 120
    assert files[-1]["fileName"] == "page1-19.raw"
    assert fake_client.calls == [
        ("/projects/PXD000003/files", {"pageSize": 100, "page": 0}),
        ("/projects/PXD000003/files", {"pageSize": 100, "page": 1}),
    ]


def test_first_download_url_prefers_ftp_or_http_over_aspera():
    file_record = {
        "publicFileLocations": [
            {"value": "prd_ascp@fasp.ebi.ac.uk:pride/data/archive/2022/02/PXD028735/experimental-design.sdrf.tsv"},
            {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/experimental-design.sdrf.tsv"},
        ]
    }

    assert (
        PrideClient.first_download_url(file_record)
        == "https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/experimental-design.sdrf.tsv"
    )


def test_download_text_retries_incomplete_chunked_response(monkeypatch):
    client = PrideClient(retries=3)
    calls = 0

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            )
        return _FakeResponse({}, text="source name\tcomment[instrument]\n")

    monkeypatch.setattr(client._client, "get", fake_get)
    monkeypatch.setattr("agent.pride.client.sleep", lambda *_args: None)

    assert client.download_text("https://ftp.pride.ebi.ac.uk/example.sdrf.tsv").startswith("source name")
    assert calls == 2


def test_search_projects_retries_incomplete_chunked_response(monkeypatch):
    client = PrideClient(retries=3)
    calls = 0

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            )
        return _FakeResponse([{"accession": "PXD000001"}])

    monkeypatch.setattr(client._client, "get", fake_get)
    monkeypatch.setattr("agent.pride.client.sleep", lambda *_args: None)

    assert client.search_projects("human") == [{"accession": "PXD000001"}]
    assert calls == 2
