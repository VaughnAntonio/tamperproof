"""tamperproof: prove, not guess, when an AI coding agent games the test suite."""

from .audit import AuditResult, audit

__all__ = ["audit", "AuditResult"]
__version__ = "0.1.0"
