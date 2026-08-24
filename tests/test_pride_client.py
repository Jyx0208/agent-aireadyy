from __future__ import annotations

import httpx
import pytest

from agent.pride.client import (
    PrideClient,
    PrideInvalidResponseError,
    PridePaginationState,
    list_project_files_paginated_with_state,
    search_projects_paginated,
    search_projects_paginated_with_state,
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


def test_exhaustive_compat_search_accepts_provably_short_legacy_page() -> None:
    class LegacyClient:
        def search_projects(self, _keyword: str, page_size: int = 100):
            return [{"accession": "PXD000001"}][:page_size]

    result = search_projects_paginated_with_state(
        LegacyClient(),
        "human",
        mode="exhaustive",
        page_size=100,
    )

    assert result.records == [{"accession": "PXD000001"}]
    assert result.state.exhausted is True
    assert result.state.truncated is False
    assert result.state.stop_reason == "short_page"


def test_exhaustive_compat_search_pages_a_cursor_capable_legacy_client() -> None:
    class LegacyPagedClient:
        def search_projects(
            self,
            _keyword: str,
            page_size: int = 100,
            *,
            page: int = 0,
        ):
            if page == 0:
                return [
                    {"accession": f"PXD{index:06d}"}
                    for index in range(page_size)
                ]
            if page == 1:
                return [{"accession": "PXD999999"}]
            return []

    result = search_projects_paginated_with_state(
        LegacyPagedClient(),
        "human",
        mode="exhaustive",
        page_size=100,
    )

    assert len(result.records) == 101
    assert result.state.pages_completed == 2
    assert result.state.exhausted is True
    assert result.state.stop_reason == "short_page"


def test_exhaustive_compat_search_does_not_confuse_page_size_with_page() -> None:
    class PageOnlyClient:
        def search_projects(
            self,
            _keyword: str,
            *,
            page: int = 0,
        ):
            if page == 0:
                return [
                    {"accession": "PXD000001"},
                    {"accession": "PXD000002"},
                ]
            return [{"accession": "PXD000003"}]

    result = search_projects_paginated_with_state(
        PageOnlyClient(),
        "human",
        mode="exhaustive",
        page_size=2,
    )

    assert [row["accession"] for row in result.records] == [
        "PXD000001",
        "PXD000002",
        "PXD000003",
    ]
    assert result.state.pages_completed == 2
    assert result.state.exhausted is True


def test_exhaustive_compat_search_does_not_trust_kwargs_as_a_page_cursor() -> None:
    class KwargsOnlyClient:
        def search_projects(
            self,
            _keyword: str,
            page_size: int = 100,
            **_kwargs,
        ):
            return [
                {"accession": f"PXD{index:06d}"}
                for index in range(page_size)
            ]

    with pytest.raises(RuntimeError, match="cannot request the next page"):
        search_projects_paginated_with_state(
            KwargsOnlyClient(),
            "human",
            mode="exhaustive",
            page_size=100,
        )


def test_exhaustive_compat_search_rejects_a_cursor_that_never_advances() -> None:
    class IgnoredPageClient:
        def search_projects(
            self,
            _keyword: str,
            page_size: int = 100,
            *,
            page: int = 0,
        ):
            del page
            return [
                {"accession": f"PXD{index:06d}"}
                for index in range(page_size)
            ]

    with pytest.raises(RuntimeError, match="same full page repeatedly"):
        search_projects_paginated_with_state(
            IgnoredPageClient(),
            "human",
            mode="exhaustive",
            page_size=100,
        )


def test_budgeted_modern_search_without_page_callback_reports_estimated_progress() -> None:
    class ModernClientWithoutProgress:
        def search_projects(
            self,
            _keyword: str,
            page_size: int,
            *,
            max_pages: int,
            max_results: int,
            start_page: int = 0,
        ):
            del page_size, max_pages, start_page
            return [
                {"accession": f"PXD{index:06d}"}
                for index in range(max_results)
            ]

    progress: list[tuple[int, int, int]] = []
    result = search_projects_paginated_with_state(
        ModernClientWithoutProgress(),
        "human",
        mode="budgeted",
        page_size=100,
        max_pages=2,
        max_results=150,
        on_page=lambda page, count, cumulative: progress.append(
            (page, count, cumulative)
        ),
    )

    assert len(result.records) == 150
    assert result.state.pages_completed == 2
    assert result.state.last_page_count == 50
    assert result.state.page_progress_exact is False
    assert result.state.exhausted is False
    assert result.state.truncated is True
    assert progress == [(1, 100, 100), (2, 50, 150)]


def test_unavailable_pagination_uses_the_same_public_schema_as_success() -> None:
    unavailable = PridePaginationState.unavailable(
        operation="search_projects",
        mode="budgeted",
        query_text="human",
        keyword="human",
        page_size=100,
        stop_reason="error",
    )

    assert unavailable.mode == "budgeted"
    assert unavailable.keyword == "human"
    assert unavailable.pages_completed == 0
    assert unavailable.exhausted is False
    assert unavailable.truncated is True
    assert set(unavailable.to_dict()) == set(
        search_projects_paginated_with_state(
            type(
                "LegacyClient",
                (),
                {"search_projects": lambda self, keyword, page_size=100: []},
            )(),
            "human",
            mode="exhaustive",
            page_size=100,
        ).state.to_dict()
    )


def test_exhaustive_compat_file_inventory_accepts_provably_short_legacy_page() -> None:
    class LegacyClient:
        def list_project_files(
            self,
            _accession: str,
            keyword: str | None = None,
            page_size: int = 1000,
            max_files: int | None = None,
        ):
            del keyword, page_size, max_files
            return [{"fileName": "sample.raw"}]

    result = list_project_files_paginated_with_state(
        LegacyClient(),
        "PXD000001",
        mode="exhaustive",
        page_size=100,
    )

    assert result.records == [{"fileName": "sample.raw"}]
    assert result.state.exhausted is True
    assert result.state.truncated is False


def test_exhaustive_compat_file_inventory_pages_a_cursor_capable_legacy_client() -> None:
    class LegacyPagedClient:
        def list_project_files(
            self,
            _accession: str,
            keyword: str | None = None,
            page_size: int = 100,
            max_files: int | None = None,
            *,
            page: int = 0,
        ):
            del keyword, max_files
            if page == 0:
                return [
                    {"fileName": f"sample-{index:03d}.raw"}
                    for index in range(page_size)
                ]
            if page == 1:
                return [{"fileName": "last.raw"}]
            return []

    result = list_project_files_paginated_with_state(
        LegacyPagedClient(),
        "PXD000001",
        mode="exhaustive",
        page_size=100,
    )

    assert len(result.records) == 101
    assert result.state.pages_completed == 2
    assert result.state.exhausted is True
    assert result.state.stop_reason == "short_page"


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


def test_search_projects_without_limits_is_exhaustive():
    fake_client = _FakeHTTPClient(
        {
            0: [{"accession": f"PXD0{index:05d}"} for index in range(100)],
            1: [{"accession": "PXD100000"}],
        }
    )
    client = PrideClient()
    client._client = fake_client

    projects = client.search_projects("human", page_size=100)

    assert len(projects) == 101
    assert fake_client.calls == [
        ("/search/projects", {"keyword": "human", "pageSize": 100, "page": 0}),
        ("/search/projects", {"keyword": "human", "pageSize": 100, "page": 1}),
    ]


def test_budgeted_project_search_reports_truncation_and_resume_page():
    client = PrideClient()
    client._client = _FakeHTTPClient(
        {
            0: [{"accession": f"PXD0{index:05d}"} for index in range(100)],
            1: [{"accession": f"PXD1{index:05d}"} for index in range(100)],
        }
    )

    result = client.search_projects_with_state(
        "human",
        mode="budgeted",
        page_size=100,
        max_pages=1,
    )

    assert len(result.records) == 100
    assert result.state.exhausted is False
    assert result.state.truncated is True
    assert result.state.next_page == 1
    assert result.state.pages_completed == 1
    assert result.state.records_seen == 100
    assert result.state.unique_records_seen == 100
    assert result.state.last_page_count == 100
    assert result.state.stop_reason == "max_pages"


def test_budgeted_project_result_limit_resumes_inside_page_without_loss():
    pages = {
        0: [{"accession": f"PXD0{index:05d}"} for index in range(100)],
        1: [{"accession": f"PXD1{index:05d}"} for index in range(100)],
        2: [{"accession": "PXD200000"}],
    }
    client = PrideClient()
    client._client = _FakeHTTPClient(pages)

    first = client.search_projects_with_state(
        "human",
        mode="budgeted",
        page_size=100,
        max_results=120,
    )

    assert len(first.records) == 120
    assert first.state.next_page == 1
    assert first.state.next_page_offset == 20

    resumed_http = _FakeHTTPClient(pages)
    client._client = resumed_http
    resumed = client.search_projects_with_state(
        "human",
        mode="exhaustive",
        page_size=100,
        start_page=first.state.next_page,
        start_page_offset=first.state.next_page_offset,
    )

    accessions = [row["accession"] for row in [*first.records, *resumed.records]]
    assert len(accessions) == 201
    assert len(set(accessions)) == 201
    assert resumed.state.exhausted is True
    assert resumed_http.calls[0] == (
        "/search/projects",
        {"keyword": "human", "pageSize": 100, "page": 1},
    )


@pytest.mark.parametrize("target_page", [1, 4, 19])
def test_exhaustive_project_search_finds_targets_on_late_pages(target_page: int):
    pages = {
        page: [{"accession": f"PXD{page:06d}"}]
        for page in range(target_page)
    }
    pages[target_page] = [{"accession": "PXD_TARGET"}]
    pages[target_page + 1] = []
    client = PrideClient()
    fake_http = _FakeHTTPClient(pages)
    client._client = fake_http

    result = client.search_projects_with_state(
        "late target",
        mode="exhaustive",
        page_size=1,
    )

    assert any(row["accession"] == "PXD_TARGET" for row in result.records)
    assert result.state.exhausted is True
    assert fake_http.calls[-1][1]["page"] == target_page + 1


def test_duplicate_full_project_page_does_not_claim_exhaustion():
    duplicate_page = [
        {"accession": "PXD000001"},
        {"accession": "PXD000002"},
    ]
    client = PrideClient()
    fake_http = _FakeHTTPClient(
        {
            0: duplicate_page,
            1: duplicate_page,
            2: [{"accession": "PXD_TARGET"}],
        }
    )
    client._client = fake_http

    result = client.search_projects_with_state(
        "duplicates",
        mode="exhaustive",
        page_size=2,
    )

    assert result.records[-1]["accession"] == "PXD_TARGET"
    assert result.state.records_seen == 5
    assert result.state.unique_records_seen == 3
    assert result.state.exhausted is True
    assert [params["page"] for _path, params in fake_http.calls] == [0, 1, 2]


def test_exhaustive_project_search_resumes_from_page_seven():
    client = PrideClient()
    fake_http = _FakeHTTPClient({7: [{"accession": "PXD_PAGE_7"}]})
    client._client = fake_http

    result = client.search_projects_with_state(
        "resume",
        mode="exhaustive",
        page_size=100,
        start_page=7,
    )

    assert result.records == [{"accession": "PXD_PAGE_7"}]
    assert fake_http.calls == [
        ("/search/projects", {"keyword": "resume", "pageSize": 100, "page": 7})
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


def test_budgeted_file_inventory_resumes_inside_a_partially_consumed_page():
    pages = {
        0: [{"fileName": f"page0-{index}.raw"} for index in range(100)],
        1: [{"fileName": f"page1-{index}.raw"} for index in range(100)],
        2: [{"fileName": "page2-0.raw"}],
    }
    client = PrideClient()
    first_http = _FakeHTTPClient(pages)
    client._client = first_http

    first = client.list_project_files_with_state(
        "PXD000004",
        mode="budgeted",
        page_size=100,
        max_files=120,
    )

    assert len(first.records) == 120
    assert first.state.truncated is True
    assert first.state.exhausted is False
    assert first.state.next_page == 1
    assert first.state.next_page_offset == 20
    assert first.state.stop_reason == "max_files"

    second_http = _FakeHTTPClient(pages)
    client._client = second_http
    resumed = client.list_project_files_with_state(
        "PXD000004",
        mode="exhaustive",
        page_size=100,
        start_page=first.state.next_page,
        start_page_offset=first.state.next_page_offset,
    )

    combined_names = [row["fileName"] for row in [*first.records, *resumed.records]]
    assert len(combined_names) == 201
    assert len(set(combined_names)) == 201
    assert resumed.state.exhausted is True
    assert second_http.calls[0] == (
        "/projects/PXD000004/files",
        {"pageSize": 100, "page": 1},
    )


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
