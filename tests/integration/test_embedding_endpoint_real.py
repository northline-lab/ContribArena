from __future__ import annotations

import base64
import os
import unittest

import httpx


def _require_enabled() -> None:
    if os.environ.get("RUN_EMBEDDING_INTEGRATION") != "1":
        raise unittest.SkipTest("set RUN_EMBEDDING_INTEGRATION=1 to run real embedding tests")
    if not os.environ.get("CONTRIBARENA_GRAPHITI_EMBEDDING_BASE_URL"):
        raise unittest.SkipTest("CONTRIBARENA_GRAPHITI_EMBEDDING_BASE_URL is required")


class RealEmbeddingEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        _require_enabled()
        self.base_url = os.environ["CONTRIBARENA_GRAPHITI_EMBEDDING_BASE_URL"].rstrip("/")
        self.model = os.environ.get("CONTRIBARENA_GRAPHITI_EMBEDDING_MODEL", "qwen3-vl-embed")
        dim = os.environ.get("CONTRIBARENA_GRAPHITI_EMBEDDING_DIM", "")
        self.expected_dim = int(dim) if dim.strip() else None
        self.headers = {}
        api_key = os.environ.get("CONTRIBARENA_GRAPHITI_EMBEDDING_API_KEY")
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def test_text_embedding_contract(self) -> None:
        vector = self._embed("ContribArena Graphiti memory retrieval smoke test.")

        self.assertGreater(len(vector), 0)
        if self.expected_dim is not None:
            self.assertEqual(self.expected_dim, len(vector))

    def test_image_data_url_embedding_contract(self) -> None:
        vector = self._embed(_one_pixel_png_data_url())

        self.assertGreater(len(vector), 0)
        if self.expected_dim is not None:
            self.assertEqual(self.expected_dim, len(vector))

    def _embed(self, text_or_data_url: str) -> list[float]:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/embeddings",
                headers=self.headers,
                json={"model": self.model, "input": text_or_data_url},
            )
        response.raise_for_status()
        payload = response.json()
        return payload["data"][0]["embedding"]


def _one_pixel_png_data_url() -> str:
    # 1x1 transparent PNG.
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


if __name__ == "__main__":
    unittest.main()
