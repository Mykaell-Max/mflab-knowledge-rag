from __future__ import annotations

import unittest
from importlib import metadata
from unittest import mock

import mflab_knowledge


class VersionTests(unittest.TestCase):
    def test_package_version_comes_from_distribution_metadata(self) -> None:
        with mock.patch(
            "mflab_knowledge.version",
            return_value="9.8.7",
        ):
            actual = mflab_knowledge._distribution_version()

        self.assertEqual(actual, "9.8.7")

    def test_uninstalled_source_tree_has_safe_version_fallback(self) -> None:
        with mock.patch(
            "mflab_knowledge.version",
            side_effect=metadata.PackageNotFoundError,
        ):
            actual = mflab_knowledge._distribution_version()

        self.assertEqual(actual, "0+uninstalled")


if __name__ == "__main__":
    unittest.main()
