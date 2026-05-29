from odds_scraper.reconstruct import report


def test_build_report_contains_counts_and_split():
    md = report.build_report(
        source_table="bi_Samuel.t", output_table="risk_Lorenzo.o",
        n_out=2, n_1up=1, n_prematch=1, n_live=1,
        staleness_samples=[100, 200], staleness_max=200, flagged_drift=1)
    assert "bi_Samuel.t" in md and "risk_Lorenzo.o" in md
    assert "rows emitted" in md
    assert "prematch" in md and "live" in md
    assert "max_lead" in md.lower()  # limitation note present
