from pathlib import Path

from agent.input.normalizer import normalize_input


def test_normalize_local_path():
    task = normalize_input(r"C:\data\Example_File.RAW")

    assert task.source_type == "local_path"
    assert task.file_name == "Example_File.RAW"
    assert task.stem == "Example_File"
    assert task.extension == ".raw"
    assert task.normalized_name == "example-file.raw"


def test_normalize_url():
    task = normalize_input("https://example.org/archive/Sample_01.mzML")

    assert task.source_type == "url"
    assert task.file_name == "Sample_01.mzML"
    assert task.stem == "Sample_01"
    assert task.extension == ".mzml"
    assert task.normalized_name == "sample-01.mzml"


def test_normalize_compound_compressed_extension():
    task = normalize_input("https://example.org/archive/Sample_01.mzML.gz")

    assert task.source_type == "url"
    assert task.file_name == "Sample_01.mzML.gz"
    assert task.stem == "Sample_01"
    assert task.extension == ".mzml.gz"
    assert task.normalized_name == "sample-01.mzml.gz"


def test_normalize_bare_filename():
    task = normalize_input("Sample_Only.d")

    assert task.source_type == "file_name"
    assert task.file_name == "Sample_Only.d"
    assert task.stem == "Sample_Only"
    assert task.extension == ".d"
    assert Path(task.original_input).name == "Sample_Only.d"
