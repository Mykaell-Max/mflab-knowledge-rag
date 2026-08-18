"""MFLab Knowledge RAG."""

from importlib.metadata import PackageNotFoundError, version


def _distribution_version() -> str:
    try:
        return version("mflab-knowledge-rag")
    except PackageNotFoundError:
        return "0+uninstalled"


__version__ = _distribution_version()
