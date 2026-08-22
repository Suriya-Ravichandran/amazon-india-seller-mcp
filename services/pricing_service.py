"""Amazon India profitability, fee breakdown and price recommendation logic.

Every fee line item is returned with ``fee_type``, ``amount``, ``source``,
``effective_date`` and ``data_type`` so a seller can see exactly which numbers
are verified and which are approximations from the bundled schedule.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from config.settings import FeeSchedule, Settings, get_settings
from services import Confidence, DataEnvelope, DataType, InvalidInputError, ServiceError

logger = logging.getLogger(__name__)

FulfillmentMethod = Literal["FBA", "Easy Ship", "Self Ship"]
FULFILMENT_KEYS: dict[str, str] = {
    "fba": "fba",
    "easy ship": "easy_ship",
    "easyship": "easy_ship",
    "self ship": "self_ship",
    "selfship": "self_ship",
    "mfn": "self_ship",
}


class FeeLine(BaseModel):
    """A single fee component with full provenance."""

    fee_type: str
    amount: float
    source: str
    effective_date: str
    data_type: str
    basis: str | None = None


class ProfitBreakdown(BaseModel):
    """Complete per-order profitability result."""

    selling_price: float
    product_cost: float
    packaging_cost: float
    amazon_fees: float
    fulfillment_cost: float
    return_reserve: float
    other_costs: float
    total_cost: float
    estimated_profit: float
    profit_margin: float
    roi: float
    break_even_price: float
    recommended_selling_price: float
    fee_lines: list[FeeLine] = Field(default_factory=list)
    fulfillment_method: str = "FBA"
    profitability_score: float = 0.0


class PricingService:
    """Reusable Amazon India pricing and profitability calculator."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.fees: FeeSchedule = self.settings.fee_schedule

    # -- validation ------------------------------------------------------- #
    @staticmethod
    def normalise_fulfillment(method: str) -> tuple[str, str]:
        """Return ``(display_name, schedule_key)`` for a fulfilment method."""
        key = FULFILMENT_KEYS.get((method or "FBA").strip().lower())
        if key is None:
            raise InvalidInputError(
                f"Invalid fulfillment_method '{method}'.",
                remediation="Use 'FBA', 'Easy Ship' or 'Self Ship'.",
            )
        display = {"fba": "FBA", "easy_ship": "Easy Ship", "self_ship": "Self Ship"}[key]
        return display, key

    def _validate_inputs(
        self,
        selling_price: float,
        product_cost: float,
        packaging_cost: float,
        weight_grams: float,
        expected_return_rate: float,
        other_costs: float,
    ) -> None:
        if selling_price <= 0:
            raise InvalidInputError("Invalid selling price: it must be greater than 0.")
        if selling_price > 500_000:
            raise InvalidInputError("Invalid selling price: value looks unrealistic (over ₹5,00,000).")
        if product_cost < 0 or packaging_cost < 0 or other_costs < 0:
            raise InvalidInputError("Invalid cost: costs cannot be negative.")
        if weight_grams <= 0:
            raise InvalidInputError("Invalid weight: weight_grams must be greater than 0.")
        if weight_grams > 100_000:
            raise InvalidInputError("Invalid weight: values above 100 kg are not supported.")
        if not 0 <= expected_return_rate <= 0.9:
            raise InvalidInputError("Invalid expected_return_rate: use a fraction between 0 and 0.9 (e.g. 0.05 for 5%).")

    # -- fee components --------------------------------------------------- #
    def _fee_line(self, fee_type: str, amount: float, basis: str) -> FeeLine:
        return FeeLine(
            fee_type=fee_type,
            amount=round(amount, 2),
            source=self.fees.source,
            effective_date=self.fees.effective_date.isoformat(),
            data_type=self.fees.data_type,
            basis=basis,
        )

    def fee_breakdown(
        self,
        selling_price: float,
        weight_grams: float,
        category: str,
        fulfilment_key: str,
        shipping_cost_override: float | None = None,
    ) -> tuple[list[FeeLine], float, float]:
        """Return ``(fee_lines, amazon_fees_total, fulfilment_cost)``."""
        referral_rate, matched_category = self.fees.referral_rate_for(category)
        referral = selling_price * referral_rate
        closing = self.fees.closing_fee_for(selling_price, fulfilment_key)

        if fulfilment_key == "self_ship" and shipping_cost_override is not None:
            fulfilment = float(shipping_cost_override)
            fulfilment_basis = "Seller supplied courier cost"
        else:
            fulfilment = self.fees.fulfilment_fee_for(weight_grams, fulfilment_key)
            fulfilment_basis = f"{weight_grams:.0f} g slab, {fulfilment_key.replace('_', ' ')}"

        gst_base = referral + closing + fulfilment
        gst = gst_base * self.fees.gst_rate_on_fees

        lines = [
            self._fee_line("Referral Fee", referral, f"{referral_rate * 100:.1f}% of selling price ({matched_category})"),
            self._fee_line("Closing Fee", closing, f"Price slab for ₹{selling_price:.0f}"),
            self._fee_line(
                "FBA Fulfilment Cost" if fulfilment_key == "fba"
                else "Easy Ship Cost" if fulfilment_key == "easy_ship"
                else "Self Ship Shipping Cost",
                fulfilment,
                fulfilment_basis,
            ),
            self._fee_line("GST on Amazon Fees", gst, f"{self.fees.gst_rate_on_fees * 100:.0f}% on referral + closing + fulfilment"),
        ]
        if self.fees.payment_gateway_rate:
            gateway = selling_price * self.fees.payment_gateway_rate
            lines.append(self._fee_line("Payment Gateway Fee", gateway, f"{self.fees.payment_gateway_rate * 100:.2f}% of selling price"))

        amazon_fees = sum(line.amount for line in lines)
        return lines, round(amazon_fees, 2), round(fulfilment, 2)

    # -- main calculation ------------------------------------------------- #
    def calculate(
        self,
        selling_price: float,
        product_cost: float,
        packaging_cost: float = 0.0,
        weight_grams: float = 250.0,
        fulfillment_method: str = "FBA",
        category: str = "Home & Kitchen",
        expected_return_rate: float = 0.05,
        other_costs: float = 0.0,
        shipping_cost_override: float | None = None,
        target_margin: float | None = None,
    ) -> ProfitBreakdown:
        """Compute the full per-order profit breakdown."""
        self._validate_inputs(
            selling_price, product_cost, packaging_cost, weight_grams, expected_return_rate, other_costs
        )
        display_method, fulfilment_key = self.normalise_fulfillment(fulfillment_method)
        target_margin = self.settings.beginner_criteria.min_profit_margin if target_margin is None else target_margin

        fee_lines, amazon_fees, fulfilment_cost = self.fee_breakdown(
            selling_price, weight_grams, category, fulfilment_key, shipping_cost_override
        )
        # Fees list already contains the fulfilment cost; keep it visible separately
        # but do not double count it in the total.
        amazon_fees_excl_fulfilment = round(amazon_fees - fulfilment_cost, 2)

        return_reserve = round(
            expected_return_rate * (product_cost + packaging_cost + fulfilment_cost * 1.5), 2
        )
        total_cost = round(
            product_cost + packaging_cost + amazon_fees_excl_fulfilment + fulfilment_cost + return_reserve + other_costs,
            2,
        )
        profit = round(selling_price - total_cost, 2)
        margin = round(profit / selling_price, 4) if selling_price else 0.0
        invested = product_cost + packaging_cost + other_costs
        roi = round(profit / invested, 4) if invested > 0 else 0.0

        fixed_costs = product_cost + packaging_cost + other_costs + return_reserve
        break_even = self._solve_price(fixed_costs, weight_grams, category, fulfilment_key, 0.0, shipping_cost_override)
        recommended = self._solve_price(
            fixed_costs, weight_grams, category, fulfilment_key, target_margin, shipping_cost_override
        )

        return ProfitBreakdown(
            selling_price=round(selling_price, 2),
            product_cost=round(product_cost, 2),
            packaging_cost=round(packaging_cost, 2),
            amazon_fees=amazon_fees_excl_fulfilment,
            fulfillment_cost=fulfilment_cost,
            return_reserve=return_reserve,
            other_costs=round(other_costs, 2),
            total_cost=total_cost,
            estimated_profit=profit,
            profit_margin=margin,
            roi=roi,
            break_even_price=break_even,
            recommended_selling_price=recommended,
            fee_lines=fee_lines,
            fulfillment_method=display_method,
            profitability_score=profitability_score(margin, roi, self.settings.beginner_criteria.min_profit_margin),
        )

    def _solve_price(
        self,
        fixed_costs: float,
        weight_grams: float,
        category: str,
        fulfilment_key: str,
        target_margin: float,
        shipping_cost_override: float | None,
    ) -> float:
        """Solve for the price that hits ``target_margin`` (0 = break-even).

        The referral fee is proportional to price while the closing fee comes
        from a price slab, so the equation is solved analytically then iterated a
        few times to settle on the correct slab.
        """
        referral_rate, _ = self.fees.referral_rate_for(category)
        gst = self.fees.gst_rate_on_fees
        gateway = self.fees.payment_gateway_rate
        variable_rate = referral_rate * (1 + gst) + gateway
        denominator = 1 - variable_rate - target_margin
        if denominator <= 0:
            raise ServiceError(
                "Target margin is unreachable with the configured fee rates.",
                remediation="Lower the target margin or choose a lower referral fee category.",
            )

        price = max(1.0, fixed_costs / denominator)
        for _ in range(6):
            if fulfilment_key == "self_ship" and shipping_cost_override is not None:
                fulfilment = float(shipping_cost_override)
            else:
                fulfilment = self.fees.fulfilment_fee_for(weight_grams, fulfilment_key)
            closing = self.fees.closing_fee_for(price, fulfilment_key)
            flat = fixed_costs + (closing + fulfilment) * (1 + gst)
            new_price = flat / denominator
            if abs(new_price - price) < 0.01:
                price = new_price
                break
            price = new_price
        return round(price, 2)

    # -- explanation ------------------------------------------------------ #
    def explain(self, breakdown: ProfitBreakdown) -> dict[str, Any]:
        """Beginner-friendly interpretation of a profit breakdown."""
        criteria = self.settings.beginner_criteria
        margin_pct = breakdown.profit_margin * 100
        target_pct = criteria.min_profit_margin * 100
        cost_items = {
            "Product Cost": breakdown.product_cost,
            "Packaging Cost": breakdown.packaging_cost,
            "Amazon Fees": breakdown.amazon_fees,
            "Fulfilment Cost": breakdown.fulfillment_cost,
            "Return Reserve": breakdown.return_reserve,
            "Other Costs": breakdown.other_costs,
        }
        biggest = max(cost_items.items(), key=lambda item: item[1])
        headroom = round(breakdown.selling_price - breakdown.break_even_price, 2)
        headroom_pct = round(headroom / breakdown.selling_price * 100, 1) if breakdown.selling_price else 0.0

        if breakdown.estimated_profit <= 0:
            verdict = "No - this product loses money at the current price and cost."
            launch = "Do not launch at these numbers. Reduce product cost or raise the price."
        elif margin_pct >= target_pct:
            verdict = f"Yes - it clears the {target_pct:.0f}% beginner margin target at {margin_pct:.1f}%."
            launch = "Reasonable to launch, provided demand and competition also check out."
        elif margin_pct >= target_pct * 0.6:
            verdict = f"Marginal - {margin_pct:.1f}% margin is below the {target_pct:.0f}% target."
            launch = "Only launch if you can cut product cost, or sell a multi-pack at a higher price."
        else:
            verdict = f"Weak - {margin_pct:.1f}% margin leaves almost no room for ads or returns."
            launch = "Not recommended without a materially cheaper source."

        return {
            "is_this_product_profitable": verdict,
            "should_the_seller_launch_it": launch,
            "biggest_cost": f"{biggest[0]} at ₹{biggest[1]:.2f} ({biggest[1] / breakdown.total_cost * 100:.0f}% of total cost)"
            if breakdown.total_cost
            else "Not applicable",
            "margin_safety_available": (
                f"You can drop the price by ₹{headroom:.2f} ({headroom_pct:.1f}%) before hitting break-even "
                f"at ₹{breakdown.break_even_price:.2f}."
            ),
            "advertising_headroom": (
                f"At {margin_pct:.1f}% margin you can spend about ₹{max(0.0, breakdown.estimated_profit * 0.4):.2f} "
                "per order on ads and still keep 60% of the profit."
            ),
            "price_band_check": (
                "Inside the ₹199-₹699 beginner price band."
                if criteria.min_selling_price_inr <= breakdown.selling_price <= criteria.max_selling_price_inr
                else f"Outside the ₹{criteria.min_selling_price_inr:.0f}-₹{criteria.max_selling_price_inr:.0f} beginner price band."
            ),
        }

    def envelope(self) -> DataEnvelope:
        """Provenance for the fee schedule currently in use."""
        return DataEnvelope(
            source=self.fees.source,
            data_type=DataType(self.fees.data_type),
            confidence=Confidence.MEDIUM if self.fees.data_type == "Verified" else Confidence.LOW,
            notes=(
                "Fees come from the configured schedule effective "
                f"{self.fees.effective_date.isoformat()}. Replace them with your Seller Central rate card "
                "(AMAZON_FEE_CONFIG_PATH) before making a real purchase decision."
            ),
        )


def profitability_score(margin: float, roi: float, target_margin: float) -> float:
    """0-100 profitability sub-score used by the opportunity model."""
    margin_component = max(0.0, min(1.0, margin / (target_margin * 1.5 or 0.45))) * 65
    roi_component = max(0.0, min(1.0, roi / 1.0)) * 35
    return round(max(0.0, min(100.0, margin_component + roi_component)), 1)
