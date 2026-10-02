"""Desk settings: where it reads, where its (disposable) cache lives, where it listens."""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DESK_DIRNAME = "private_desk"


class RemoteBindRefused(ValueError):
    """Phase 1 is local only: a non-loopback bind address is refused outright."""


def require_loopback(host: str) -> str:
    """`127.0.0.1`, `::1` or `localhost` - anything else (incl. `0.0.0.0`) raises."""
    if host == "localhost":
        return host
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise RemoteBindRefused(
        f"refusing to bind {host!r}: the Private Desk is local only (use 127.0.0.1)")


@dataclass(frozen=True)
class DeskSettings:
    out_dir: str                 # the DMB output root the desk READS (config.OUT_DIR)
    cache_dir: str               # the only place the desk WRITES (regeneratable)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @classmethod
    def from_out_dir(cls, out_dir: str | None = None, *, cache_dir: str | None = None,
                     host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> "DeskSettings":
        if out_dir is None:
            import config
            out_dir = config.OUT_DIR
        out_dir = os.path.abspath(out_dir)
        cache_dir = os.path.abspath(cache_dir or os.path.join(out_dir, DESK_DIRNAME, "cache"))
        return cls(out_dir=out_dir, cache_dir=cache_dir, host=require_loopback(host), port=port)

    @property
    def regime_dir(self) -> str:
        """Derived regime snapshots: a sibling of the cache (`<OUT>/private_desk/regime` by
        default) - same rules as the cache: regeneratable, never production state."""
        return os.path.join(os.path.dirname(self.cache_dir), "regime")

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


__all__ = ["DeskSettings", "require_loopback", "RemoteBindRefused", "DEFAULT_HOST",
          "DEFAULT_PORT", "DESK_DIRNAME"]
