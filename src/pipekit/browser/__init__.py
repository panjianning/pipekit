"""Browser subpackage — session management, context store, atomic actions."""

from .contexts import ContextStore
from .session import BrowserSession, _pipekit_root

__all__ = ["BrowserSession", "ContextStore", "_pipekit_root"]
