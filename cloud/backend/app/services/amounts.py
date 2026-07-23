"""The voucher's amount, in one place: the grid, History, and the Tally XML
must all show the same number."""


def display_amount(invoice: dict):
    """The number shown in the grid's Amount box (vouchers.js). A GRN carries a
    Net GRN Amount that differs from the Tax Invoice total; a plain invoice
    leaves it at 0, so the total stands."""
    return invoice.get("Net GRN Amount") or invoice.get("total_amount")
