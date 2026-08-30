"""Finalized-candle verification for realtime strategy sessions.

The package keeps the verification subsystem separate from the primary runner:

* ``source`` selects exchange-confirmed candles.
* ``coordinator`` owns session warm-up, replay, and result comparison.
* ``protocol`` normalizes comparison identities shared by those paths.

Process creation remains in ``runner_supervisor`` and the isolated strategy
calculation remains in ``runner_service`` because those are process boundaries.
"""

from .coordinator import VerificationCoordinator
from .source import FinalizedCandleProbe

__all__ = ["FinalizedCandleProbe", "VerificationCoordinator"]
