from odds_scraper.reconstruct import report


def test_build_report_contains_counts_and_split():
    sample = [
        {"in_play": False, "has_1up": True, "max_input_staleness_seconds": 100,
         "renorm_drift": 0.01},
        {"in_play": True, "has_1up": False, "max_input_staleness_seconds": 200,
         "renorm_drift": 0.2},
    ]
    md = report.build_report(
        source_table="bi_Samuel.t", output_table="risk_Lorenzo.o",
        n_out=2, n_1up=1, n_prematch=1, n_live=1,
        sample_rows=sample, flagged_drift=1)
    assert "bi_Samuel.t" in md and "risk_Lorenzo.o" in md
    assert "rows emitted" in md
    assert "prematch" in md and "live" in md
    assert "max_lead" in md.lower()  # limitation note present
