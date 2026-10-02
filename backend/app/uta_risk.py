"""User-confirmed acceptance limits. Hard limits cannot be relaxed by config."""
from decimal import Decimal

from pydantic import BaseModel, Field

from .bitget import BitgetError
from .uta import number


class UtaRiskLimits(BaseModel):
    position_percent: Decimal = Field(default=Decimal('6'),gt=0,le=7)
    # Kept for old saved settings compatibility; no longer an execution ceiling.
    max_leverage: int = Field(default=10,ge=1)
    max_positions: int = Field(default=5,ge=1,le=6)

    def margin(self,equity):
        return number(equity,'实时净值')*self.position_percent/100

    def leverage(self,requested,exchange_max):
        from .follow_policy import exchange_leverage
        return exchange_leverage(exchange_max,override=requested)

    def check_count(self,positions,reserved=0):
        # Count foreign positions too, without taking ownership of them.
        count=sum(number(r.get('total'),'实时持仓数量',positive=False)!=0 for r in positions)
        if count+reserved >= self.max_positions:
            raise BitgetError('已达到未平仓仓位上限（包含手动及其他程序持仓）')
