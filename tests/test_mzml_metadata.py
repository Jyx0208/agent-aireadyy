from pathlib import Path

from agent.inference.mzml_metadata import dda_mzml_search_blocking_issue, parse_mzml_instrument, summarize_mzml_spectra


def test_parse_mzml_instrument_reads_instrument_model(tmp_path: Path):
    mzml = tmp_path / "sample.mzML"
    mzml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="http://psi.hupo.org/ms/mzml">
  <instrumentConfigurationList count="1">
    <instrumentConfiguration id="IC1">
      <cvParam cvRef="MS" accession="MS:1002523" name="Q Exactive HF" value=""/>
      <componentList count="3">
        <source order="1">
          <cvParam cvRef="MS" accession="MS:1000073" name="electrospray ionization" value=""/>
        </source>
        <analyzer order="2">
          <cvParam cvRef="MS" accession="MS:1000484" name="orbitrap" value=""/>
        </analyzer>
      </componentList>
    </instrumentConfiguration>
  </instrumentConfigurationList>
</mzML>
""",
        encoding="utf-8",
    )

    metadata = parse_mzml_instrument(mzml)

    assert metadata is not None
    assert metadata.name == "Q Exactive HF"
    assert metadata.family == "orbitrap"
    assert "MS:1002523" in metadata.evidence


def test_summarize_mzml_spectra_counts_ms_levels(tmp_path: Path):
    mzml = tmp_path / "dda.mzML"
    mzml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="http://psi.hupo.org/ms/mzml">
  <run id="run1">
    <spectrumList count="2">
      <spectrum id="scan=1">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="1"/>
      </spectrum>
      <spectrum id="scan=2">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="2"/>
      </spectrum>
    </spectrumList>
  </run>
</mzML>
""",
        encoding="utf-8",
    )

    summary = summarize_mzml_spectra(mzml)

    assert summary is not None
    assert summary.spectrum_list_count == 2
    assert summary.ms1_count == 1
    assert summary.ms2_count == 1
    assert dda_mzml_search_blocking_issue(mzml) is None


def test_dda_mzml_search_blocking_issue_rejects_ms1_only_mzml(tmp_path: Path):
    mzml = tmp_path / "ms1_only.mzML"
    mzml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="http://psi.hupo.org/ms/mzml">
  <run id="run1">
    <spectrumList count="2">
      <spectrum id="scan=1">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="1"/>
      </spectrum>
      <spectrum id="scan=2">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="1"/>
      </spectrum>
    </spectrumList>
  </run>
</mzML>
""",
        encoding="utf-8",
    )

    issue = dda_mzml_search_blocking_issue(mzml)

    assert issue is not None
    assert "no MS2 spectra" in issue
    assert "MS1=2; MS2=0" in issue


def test_dda_mzml_search_blocking_issue_rejects_empty_mzml(tmp_path: Path):
    mzml = tmp_path / "empty.mzML"
    mzml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="http://psi.hupo.org/ms/mzml">
  <run id="run1">
    <spectrumList count="0"/>
  </run>
</mzML>
""",
        encoding="utf-8",
    )

    issue = dda_mzml_search_blocking_issue(mzml)

    assert issue is not None
    assert "contains no spectra" in issue
