"""Cross-platform process and system introspection.

Auto-detects the current platform and exposes a single ``platform``
instance with a unified API.  Backend code should never read /proc
or call platform-specific tools directly — use this module instead.
"""
import sys

if sys.platform == "linux":
    from ._linux import LinuxPlatform as _Impl
elif sys.platform == "darwin":
    from ._darwin import DarwinPlatform as _Impl
else:
    from ._darwin import DarwinPlatform as _Impl

platform = _Impl()

__all__ = ["platform"]
