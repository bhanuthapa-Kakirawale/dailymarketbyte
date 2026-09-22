"""NSE acquisition, wrapped. The exchange itself: PRIMARY, independence group NSE.

NSE is the only source in this pipeline that publishes its own market timestamp, so this is
where `observed_at` finally gets populated with something meaningful - when the exchange
says the number was true, rather than when we happened to fetch it.
"""
from __future__ import annotations

import market
from adapters.market_adapter import MarketAdapter
from core import Metric, Observation, SourceType
from core.models import UNIT_INR_CRORE, UNIT_PERCENT, UNIT_POINTS
from core.sources import SRC_NSE

from .base import Provider, ProviderResult


class NseProvider(Provider):
    name = "nse_website"

    def indices(self, nse_indices: dict) -> ProviderResult:
        """Observations for NIFTY 50 from NSE's allIndices snapshot.

        `nse_all_indices` already refused anything whose timestamp was not the recap
        session, so reaching here means the date check upstream passed; what it now also
        hands over is the timestamp itself.
        """
        row = (nse_indices or {}).get("NIFTY 50")
        if not row or self.demo:
            return ProviderResult(payload={"nse_indices": nse_indices or {}})

        observed_at = row.get("observed_at")
        common = {"market_date": self.session_date, "source_name": SRC_NSE,
                  "source_type": SourceType.PRIMARY, "retrieved_at": self.retrieved_at,
                  "observed_at": observed_at, "source_reference": "/api/allIndices"}
        observations = []
        if row.get("last") is not None:
            observations.append(Observation(
                metric=Metric.INDEX_CLOSE, instrument="NIFTY 50", value=float(row["last"]),
                unit=UNIT_POINTS, metadata={"source_timestamp": row.get("source_timestamp")},
                **common))
        if row.get("pct") is not None:
            observations.append(Observation(
                metric=Metric.INDEX_CHANGE_PCT, instrument="NIFTY 50", value=float(row["pct"]),
                unit=UNIT_PERCENT, metadata={"source_timestamp": row.get("source_timestamp")},
                **common))
        return ProviderResult(observations=observations,
                              payload={"nse_indices": nse_indices or {}})

    def fii_dii(self, flows: dict) -> ProviderResult:
        """Institutional flows. `flows["source"]` distinguishes NSE's own feed from the
        Gemini fallback, which is the difference between a PRIMARY and an AI observation."""
        adapter = MarketAdapter(self.session_date, self.retrieved_at, demo=self.demo,
                                report_date=self.report_date)
        return ProviderResult(observations=adapter.observe_fii_dii(flows),
                              payload={"fii_dii": flows or None})


__all__ = ["NseProvider"]
