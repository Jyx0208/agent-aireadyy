from pathlib import Path

from agent.inference.mzml_metadata import parse_mzml_instrument


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
