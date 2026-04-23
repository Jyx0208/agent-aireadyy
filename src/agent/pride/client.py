from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx


class PrideClient:
    def __init__(self, base_url: str = "https://www.ebi.ac.uk/pride/ws/archive/v2", timeout: float = 30.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PrideClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search_projects(self, keyword: str, page_size: int = 100) -> list[dict[str, Any]]:
        response = self._client.get("/search/projects", params={"keyword": keyword, "pageSize": page_size})
        response.raise_for_status()
        return response.json()

    def get_project(self, accession: str) -> dict[str, Any]:
        response = self._client.get(f"/projects/{accession}")
        response.raise_for_status()
        return response.json()

    def list_project_files(self, accession: str, keyword: str | None = None, page_size: int = 1000) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"pageSize": page_size}
        if keyword:
            params["keyword"] = keyword
        response = self._client.get(f"/projects/{accession}/files", params=params)
        response.raise_for_status()
        return response.json()

    def download_text(self, url: str) -> str:
        if url.startswith("ftp://ftp.pride.ebi.ac.uk/"):
            url = url.replace("ftp://ftp.pride.ebi.ac.uk/", "https://ftp.pride.ebi.ac.uk/")
        response = self._client.get(url)
        response.raise_for_status()
        return response.text

    @staticmethod
    def first_download_url(file_record: dict[str, Any]) -> str | None:
        locations: Iterable[dict[str, Any]] = file_record.get("publicFileLocations", []) or []
        for location in locations:
            value = location.get("value")
            if value:
                return value
        return None
