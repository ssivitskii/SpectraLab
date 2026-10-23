from __future__ import annotations

import hashlib

import numpy as np
import pytest
from spectralab_ml.data.chemcam_measurements import (
    MAX_CCS_BYTES,
    read_chemcam_ccs,
    read_chemcam_ccs_shots,
)


def ccs(shots=8, *, wavelengths=(350, 351), preamble="# arbitrary metadata\n"):
    header = ["wave", *(f"shot{i}" for i in range(1, shots + 1)), "median", "mean"]
    rows = [f"# NSHOTS = {shots} , , ", "# " + ", ".join(header)]
    rows += [",".join(map(str, [wave, *range(1, shots + 1), -999, 999])) for wave in wavelengths]
    return preamble + "\r\n".join(rows) + "\n"


def write(tmp_path, text):
    path = tmp_path / "measurement.csv"
    raw = text.encode() if isinstance(text, str) else text
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_header_and_shot_count_are_dynamic_and_provider_summaries_are_not_used(tmp_path):
    path, checksum = write(tmp_path, ccs())
    wavelength, spectrum, metadata = read_chemcam_ccs(path, checksum)
    np.testing.assert_array_equal(wavelength, [350, 351])
    # Discard shots 1..5, independently average shots 6,7,8. Provider fields are -999,999.
    np.testing.assert_array_equal(spectrum, [7, 7])
    assert spectrum.dtype == wavelength.dtype == np.float64
    assert metadata == {
        "shot_count": 8,
        "discarded_shots": 5,
        "retained_shots": 3,
        "aggregation": "arithmetic_mean_of_retained_shots",
    }
    path, checksum = write(tmp_path, ccs(3, preamble="# ONE\n# TWO\n# THREE\n"))
    np.testing.assert_array_equal(
        read_chemcam_ccs(path, checksum, discard_first_shots=1)[1], [2.5, 2.5]
    )


def test_raw_shot_reader_preserves_shot_identity_for_qc_before_aggregation(tmp_path):
    path, checksum = write(tmp_path, ccs(3))

    wavelength, shots, metadata = read_chemcam_ccs_shots(path, checksum)

    np.testing.assert_array_equal(wavelength, [350, 351])
    np.testing.assert_array_equal(shots, [[1, 1], [2, 2], [3, 3]])
    assert metadata == {
        "shot_count": 3,
        "input_sha256": checksum,
        "provider_summary_columns_validated": True,
    }


def test_checksum_is_verified_before_parsing_and_requires_full_hash(tmp_path):
    path, _ = write(tmp_path, "not a CSV")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_chemcam_ccs(path, "0" * 64)
    with pytest.raises(ValueError, match="complete SHA-256"):
        read_chemcam_ccs(path, "abcd")


@pytest.mark.parametrize(
    "replacement",
    [
        "# wave,shot1,shot2,shot4,median,mean",
        "# wave,shot2,shot1,shot3,median,mean",
        "# wave,shot1,shot2,shot3,mean,median",
        "# wave,shot1,shot2,shot3,median,mean,extra",
        "# wave,shot1,shot2,shot3,mean",
    ],
)
def test_nonconsecutive_reordered_and_unexpected_header_columns_are_rejected(tmp_path, replacement):
    text = ccs(3).replace("# wave, shot1, shot2, shot3, median, mean", replacement)
    path, checksum = write(tmp_path, text)
    with pytest.raises(ValueError, match="consecutive"):
        read_chemcam_ccs(path, checksum, discard_first_shots=0)


@pytest.mark.parametrize("nshots", ["-1", "0", "1001", "three", "3.0"])
def test_invalid_or_oversized_shot_count_is_rejected(tmp_path, nshots):
    path, checksum = write(tmp_path, ccs(3).replace("NSHOTS = 3", "NSHOTS = " + nshots))
    with pytest.raises(ValueError, match="NSHOTS"):
        read_chemcam_ccs(path, checksum, discard_first_shots=0)


@pytest.mark.parametrize("discard", [-1, True, 1.5, 3, 10])
def test_discard_policy_is_an_integer_and_must_leave_shots(tmp_path, discard):
    path, checksum = write(tmp_path, ccs(3))
    with pytest.raises(ValueError, match="discard_first_shots"):
        read_chemcam_ccs(path, checksum, discard_first_shots=discard)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace("350,1,2,3,-999,999", "350,1,2,-999,999"),
        lambda s: s.replace("350,1,2,3,-999,999", "350,1,2,3,-999,999,4"),
        lambda s: s.replace("350,1,2,3,-999,999", "350,1,nan,3,-999,999"),
        lambda s: s.replace("350,1,2,3,-999,999", "350,1,2,3,inf,999"),
        lambda s: s.replace("350,1,2,3,-999,999", "350,1,2,3,-999,missing"),
        lambda s: s.replace("351,1,2,3,-999,999", "350,1,2,3,-999,999"),
        lambda s: s.replace("351,1,2,3,-999,999", "349,1,2,3,-999,999"),
    ],
)
def test_numeric_shape_finiteness_and_wavelength_order_are_strict(tmp_path, change):
    path, checksum = write(tmp_path, change(ccs(3)))
    with pytest.raises(ValueError):
        read_chemcam_ccs(path, checksum, discard_first_shots=0)


def test_gaps_and_negative_radiance_are_preserved_without_interpolation(tmp_path):
    path, checksum = write(
        tmp_path, ccs(3, wavelengths=[350, 800]).replace(",1,2,3,-999,999", ",-6,-3,0,-999,999")
    )
    wave, spectrum, _ = read_chemcam_ccs(path, checksum, discard_first_shots=0)
    np.testing.assert_array_equal(wave, [350, 800])
    np.testing.assert_array_equal(spectrum, [-3, -3])


@pytest.mark.parametrize(
    "text",
    [
        "# NSHOTS = 8\n350,1,2\n",
        "# wave,shot1,median,mean\n350,1,2,3\n",
        ccs(8).replace("# arbitrary metadata", "# NSHOTS=8"),
        ccs(8) + "# NSHOTS=8\n",
        ccs(8) + "# wave,shot1,median,mean\n",
        ccs(8, wavelengths=[350]),
    ],
)
def test_missing_duplicate_and_late_headers_or_insufficient_rows_are_rejected(tmp_path, text):
    path, checksum = write(tmp_path, text)
    with pytest.raises(ValueError):
        read_chemcam_ccs(path, checksum)


def test_byte_point_and_shot_limits_are_bounded(tmp_path):
    path = tmp_path / "oversized.csv"
    with path.open("wb") as stream:
        stream.truncate(MAX_CCS_BYTES + 1)
    with pytest.raises(ValueError, match="16 MB"):
        read_chemcam_ccs(path, "0" * 64)
    path, checksum = write(tmp_path, ccs(1, wavelengths=range(100001)))
    with pytest.raises(ValueError, match="100000-point"):
        read_chemcam_ccs(path, checksum, discard_first_shots=0)
    path, checksum = write(tmp_path, ccs(1000))
    _, means, meta = read_chemcam_ccs(path, checksum, discard_first_shots=999)
    np.testing.assert_array_equal(means, [1000, 1000])
    assert meta["retained_shots"] == 1


def test_utf8_bom_is_supported_but_invalid_text_and_oversized_fields_are_rejected(tmp_path):
    path, checksum = write(tmp_path, b"\xef\xbb\xbf" + ccs().encode())
    assert len(read_chemcam_ccs(path, checksum.upper())[0]) == 2
    path, checksum = write(tmp_path, b"\xff\xfe")
    with pytest.raises(ValueError, match="UTF-8"):
        read_chemcam_ccs(path, checksum)
    path, checksum = write(tmp_path, ccs(3).replace("350,1,2,3", "350," + "1" * 150000 + ",2,3"))
    with pytest.raises(ValueError, match="Malformed CCS"):
        read_chemcam_ccs(path, checksum, discard_first_shots=0)
