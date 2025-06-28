# corps_calcs_1.py  –  Fixed-coupon corporate bond (“Calc-Type 1”)

from __future__ import annotations
import warnings
from datetime import date
from typing import List, Optional
import pandas as pd
from dateutil.relativedelta import relativedelta
from scipy import optimize


# ─────────── ACT/ACT-ISDA year-fraction (Excel basis=1) ───────────
def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _yearfrac_act_act(start: date, end: date) -> float:
    if start == end:
        return 0.0
    if start > end:
        start, end = end, start
    total, cur = 0.0, start
    while cur < end:
        nxt = date(cur.year + 1, 1, 1)
        seg_end = min(end, nxt)
        feb29 = date(cur.year, 2, 29) if _is_leap(cur.year) else None
        denom = 366 if feb29 and cur <= feb29 < seg_end else 365
        total += (seg_end - cur).days / denom
        cur = seg_end
    return total


# ─────────────────────── Bond class ───────────────────────────────
class CorpsCalcs1:
    """Spreadsheet-compatible bullet bond (Calc-Type 1)."""

    def __init__(
            self, *,
            expiry: "date | str",
            rate: Optional[float] = None,
            price: Optional[float] = None,
            principal: float = 100.0,
            coupon_rate: float = 0.05,
            freq: int = 1,
            ref_date: "date | str" = date.today(),
            first_coupon_date: Optional["date | str"] = None,
    ):
        if rate is None and price is None:
            raise ValueError("Need `rate` or `price`.")
        self.expiry = pd.to_datetime(expiry).date()
        self.ref_date = pd.to_datetime(ref_date).date()
        self.principal = float(principal)
        self.coupon_rate = float(coupon_rate)
        self.freq = int(freq)
        self.first_coupon_date = (
            pd.to_datetime(first_coupon_date).date() if first_coupon_date else None
        )
        self.cpn_amt = (self.coupon_rate / self.freq) * self.principal
        self.schedule = self._build_schedule()
        self.coupon_at_expiry = self._pays_coupon_at_expiry()

        if rate is not None:
            self.rate = float(rate)
            self.price = self._clean_from_rate(self.rate)
        else:
            self.price = float(price)
            self.rate = self._rate_from_clean(self.price)

        self.mod_duration, self.convexity = self._risk()
        self.macaulay = self.mod_duration * (1 + self.rate)
        self.dv01 = self.mod_duration * self.price / 100

    # ─────────── helpers ──────────────────────────────────────────
    def _build_schedule(self) -> List[date]:
        anchor = self.first_coupon_date or self.expiry
        step = relativedelta(months=int(12 / self.freq))
        dates, d = [], anchor
        while d < self.expiry:
            dates.append(d);
            d += step
        dates.append(self.expiry)
        return dates

    def _pays_coupon_at_expiry(self) -> bool:
        if len(self.schedule) < 2:
            return False
        prev = self.schedule[-2]
        return abs(_yearfrac_act_act(prev, self.expiry) - 1 / self.freq) < 1e-4

    # choose DF style -------------------------------------------------------
    def _df(self, y: float, t: float) -> float:
        if self.cpn_amt == 0:  # ← CHANGED
            return 1 / (1 + y) ** t  # compound for zero-coupon
        return 1 / (1 + y * t)  # simple for coupon bonds

    # pricing ---------------------------------------------------------------
    def _dirty_price(self, y: float) -> float:
        pv = 0.0
        for d in self.schedule:
            if d <= self.ref_date:
                continue
            cf = self.cpn_amt if (d != self.expiry or self.coupon_at_expiry) else 0.0
            if d == self.expiry:
                cf += self.principal
            t = _yearfrac_act_act(self.ref_date, d)
            pv += cf * self._df(y, t)  # ← CHANGED
        return pv

    def _accrued(self) -> float:
        if self.cpn_amt == 0:
            return 0.0
        prev = max(d for d in self.schedule if d <= self.ref_date)
        next_ = min(d for d in self.schedule if d > self.ref_date)
        frac = _yearfrac_act_act(prev, self.ref_date) / _yearfrac_act_act(prev, next_)
        return frac * self.cpn_amt

    def _clean_from_rate(self, y: float) -> float:
        return self._dirty_price(y) - self._accrued()

    def _rate_from_clean(self, clean: float) -> float:
        target_dirty = clean + self._accrued()

        def f(yy):
            pv = 0.0
            for d in self.schedule:
                if d <= self.ref_date:
                    continue
                cf = self.cpn_amt if (d != self.expiry or self.coupon_at_expiry) else 0.0
                if d == self.expiry:
                    cf += self.principal
                t = _yearfrac_act_act(self.ref_date, d)
                pv += cf * self._df(yy, t)  # ← CHANGED
            return pv - target_dirty

        return optimize.brentq(f, -0.95, 5.0)

    # risk ------------------------------------------------------------------
    def _risk(self):
        mdur = conv = 0.0
        for d in self.schedule:
            if d <= self.ref_date:
                continue
            cf = self.cpn_amt if (d != self.expiry or self.coupon_at_expiry) else 0.0
            if d == self.expiry:
                cf += self.principal
            t = _yearfrac_act_act(self.ref_date, d)
            df = self._df(self.rate, t)  # ← CHANGED
            pv = cf * df
            mdur += t * pv
            conv += t * (1 + t) * pv
        mdur /= self.price
        conv = (conv / self.price) / (1 + self.rate) ** 2
        return mdur, conv

    # optional table for debugging -----------------------------------------
    def cashflow_table(self) -> pd.DataFrame:
        recs = []
        for d in self.schedule:
            cup = self.cpn_amt if (d != self.expiry or self.coupon_at_expiry) else 0.0
            prin = self.principal if d == self.expiry else 0.0
            cf = cup + prin
            yrs = _yearfrac_act_act(self.ref_date, d)
            df = 0.0 if yrs <= 0 else self._df(self.rate, yrs)
            pv = cf * df if yrs > 0 else 0.0
            recs.append(dict(Date=d, Coupon=cup, Principal=prin,
                             DiscountPeriod=round(yrs, 9), DF=round(df, 9), PV=round(pv, 9)))
        df = pd.DataFrame(recs)
        df.loc["Σ", "PV"] = df["PV"].sum()
        df.loc["Σ", ["Coupon", "Principal"]] = df[["Coupon", "Principal"]].sum()
        return df


# ───────────────────── quick sanity test ──────────────────────────
if __name__ == "__main__":
    # zero-coupon
    zc = CorpsCalcs1(
        expiry="2032-02-02",
        rate=0.12101044,
        coupon_rate=0.0,
        freq=1,
        principal=100,
        ref_date="2025-07-01",
    )
    print("Zero-coupon clean:", zc.price)  # 47.09599894

    # coupon bond
    bn = CorpsCalcs1(
        expiry="2026-01-22",
        rate=0.13382347,
        coupon_rate=0.05,
        freq=1,
        principal=100,
        ref_date="2025-07-01",
        first_coupon_date="2022-01-22",
    )
    print("Fixed-coupon clean:", bn.price)  # 95.46799999

    print("Dirty  :", bn._dirty_price(bn.rate))  # 97.6597808128696
    print("Accrued:", bn._accrued())  # 2.19178082191781
    print("Clean  :", bn.price)  # 95.4679999909518

    with pd.option_context("display.float_format", "{:,.9f}".format):
        print("\nCash-flow breakdown:\n", bn.cashflow_table())
