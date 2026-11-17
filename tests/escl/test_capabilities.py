"""Unit tests for `/eSCL/ScannerCapabilities` XML parser and Brother adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from open_escl_client.escl.capabilities import (
    InputSourceKind,
    fetch_scanner_capabilities,
    mm_to_three_hundredths_inch,
    parse_scanner_capabilities_xml,
    three_hundredths_inch_to_mm,
)

# Realistic Brother MFC-L2750DW / DCP-J series eSCL ScannerCapabilities XML fixture
BROTHER_CAPABILITIES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"
                          xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
    <pwg:Version>2.63</pwg:Version>
    <pwg:MakeAndModel>Brother MFC-L2750DW series</pwg:MakeAndModel>
    <pwg:SerialNumber>E78912B3N456789</pwg:SerialNumber>
    <scan:Manufacturer>Brother</scan:Manufacturer>
    <scan:Platen>
        <scan:PlatenInputCaps>
            <scan:MinWidth>96</scan:MinWidth>
            <scan:MaxWidth>2550</scan:MaxWidth>
            <scan:MinHeight>96</scan:MinHeight>
            <scan:MaxHeight>3508</scan:MaxHeight>
            <scan:MaxOpticalXResolution>1200</scan:MaxOpticalXResolution>
            <scan:MaxOpticalYResolution>1200</scan:MaxOpticalYResolution>
            <scan:SettingProfiles>
                <scan:SettingProfile>
                    <scan:ColorModes>
                        <scan:ColorMode>BlackAndWhite1</scan:ColorMode>
                        <scan:ColorMode>Grayscale8</scan:ColorMode>
                        <scan:ColorMode>RGB24</scan:ColorMode>
                    </scan:ColorModes>
                    <scan:DocumentFormats>
                        <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
                        <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
                    </scan:DocumentFormats>
                    <scan:SupportedResolutions>
                        <scan:DiscreteResolutions>
                            <scan:DiscreteResolution>
                                <scan:XResolution>100</scan:XResolution>
                                <scan:YResolution>100</scan:YResolution>
                            </scan:DiscreteResolution>
                            <scan:DiscreteResolution>
                                <scan:XResolution>150</scan:XResolution>
                                <scan:YResolution>150</scan:YResolution>
                            </scan:DiscreteResolution>
                            <scan:DiscreteResolution>
                                <scan:XResolution>200</scan:XResolution>
                                <scan:YResolution>200</scan:YResolution>
                            </scan:DiscreteResolution>
                            <scan:DiscreteResolution>
                                <scan:XResolution>300</scan:XResolution>
                                <scan:YResolution>300</scan:YResolution>
                            </scan:DiscreteResolution>
                            <scan:DiscreteResolution>
                                <scan:XResolution>600</scan:XResolution>
                                <scan:YResolution>600</scan:YResolution>
                            </scan:DiscreteResolution>
                            <scan:DiscreteResolution>
                                <scan:XResolution>1200</scan:XResolution>
                                <scan:YResolution>1200</scan:YResolution>
                            </scan:DiscreteResolution>
                        </scan:DiscreteResolutions>
                    </scan:SupportedResolutions>
                </scan:SettingProfile>
            </scan:SettingProfiles>
        </scan:PlatenInputCaps>
    </scan:Platen>
    <scan:Adf>
        <scan:AdfSimplexInputCaps>
            <scan:MinWidth>1748</scan:MinWidth>
            <scan:MaxWidth>2550</scan:MaxWidth>
            <scan:MinHeight>1748</scan:MinHeight>
            <scan:MaxHeight>4200</scan:MaxHeight>
            <scan:SettingProfiles>
                <scan:SettingProfile>
                    <scan:ColorModes>
                        <scan:ColorMode>Grayscale8</scan:ColorMode>
                        <scan:ColorMode>RGB24</scan:ColorMode>
                    </scan:ColorModes>
                    <scan:SupportedResolutions>
                        <scan:DiscreteResolutions>
                            <scan:DiscreteResolution>
                                <scan:XResolution>100</scan:XResolution>
                                <scan:YResolution>100</scan:YResolution>
                            </scan:DiscreteResolution>
                            <scan:DiscreteResolution>
                                <scan:XResolution>300</scan:XResolution>
                                <scan:YResolution>300</scan:YResolution>
                            </scan:DiscreteResolution>
                            <scan:DiscreteResolution>
                                <scan:XResolution>600</scan:XResolution>
                                <scan:YResolution>600</scan:YResolution>
                            </scan:DiscreteResolution>
                        </scan:DiscreteResolutions>
                    </scan:SupportedResolutions>
                </scan:SettingProfile>
            </scan:SettingProfiles>
        </scan:AdfSimplexInputCaps>
        <scan:AdfDuplexInputCaps>
            <scan:MinWidth>1748</scan:MinWidth>
            <scan:MaxWidth>2550</scan:MaxWidth>
            <scan:MinHeight>1748</scan:MinHeight>
            <scan:MaxHeight>4200</scan:MaxHeight>
            <scan:SettingProfiles>
                <scan:SettingProfile>
                    <scan:SupportedResolutions>
                        <scan:DiscreteResolutions>
                            <scan:DiscreteResolution>
                                <scan:XResolution>300</scan:XResolution>
                                <scan:YResolution>300</scan:YResolution>
                            </scan:DiscreteResolution>
                        </scan:DiscreteResolutions>
                    </scan:SupportedResolutions>
                </scan:SettingProfile>
            </scan:SettingProfiles>
        </scan:AdfDuplexInputCaps>
    </scan:Adf>
</scan:ScannerCapabilities>
"""

# Generic eSCL scanner using SettingProfileRef and ResolutionRange (e.g. A3 capable scanner)
GENERIC_A3_PROFILE_REF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ScannerCapabilities xmlns="http://schemas.hp.com/imaging/escl/2011/05/03">
    <Version>2.5</Version>
    <MakeAndModel>Generic A3 AirScan Pro</MakeAndModel>
    <SettingProfiles>
        <SettingProfile name="prof_main">
            <ColorModes>
                <ColorMode>RGB24</ColorMode>
            </ColorModes>
            <SupportedResolutions>
                <ResolutionRange>
                    <XResolutionRange>
                        <Min>75</Min>
                        <Max>600</Max>
                        <Step>25</Step>
                    </XResolutionRange>
                    <YResolutionRange>
                        <Min>75</Min>
                        <Max>600</Max>
                        <Step>25</Step>
                    </YResolutionRange>
                </ResolutionRange>
            </SupportedResolutions>
        </SettingProfile>
    </SettingProfiles>
    <Platen>
        <PlatenInputCaps>
            <MinWidth>100</MinWidth>
            <MaxWidth>3508</MaxWidth>
            <MinHeight>100</MinHeight>
            <MaxHeight>4960</MaxHeight>
            <SettingProfiles>
                <SettingProfileRef>prof_main</SettingProfileRef>
            </SettingProfiles>
        </PlatenInputCaps>
    </Platen>
</ScannerCapabilities>
"""


def test_unit_conversion_helpers() -> None:
    # A4 height: 297 mm -> ~3508 (1/300 inch)
    units = mm_to_three_hundredths_inch(297.0)
    assert units == 3508
    mm_back = three_hundredths_inch_to_mm(3508)
    assert pytest.approx(mm_back, abs=0.1) == 297.0


def test_parse_brother_scanner_capabilities() -> None:
    caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)

    assert caps.make_and_model == "Brother MFC-L2750DW series"
    assert caps.is_brother is True
    assert caps.version == "2.63"
    assert caps.platen is not None
    assert caps.platen.source == InputSourceKind.PLATEN
    assert caps.platen.max_width == 2550
    assert caps.platen.max_height == 3508
    assert caps.platen.resolutions == (100, 150, 200, 300, 600, 1200)
    assert caps.platen.preview_dpi == 100
    assert "RGB24" in caps.platen.color_modes
    assert "application/pdf" in caps.platen.document_formats

    assert caps.adf_simplex is not None
    assert caps.adf_simplex.max_height == 4200
    assert caps.adf_simplex.resolutions == (100, 300, 600)

    assert caps.adf_duplex is not None
    assert len(caps.available_sources) == 3
    assert caps.primary_source.source == InputSourceKind.PLATEN


def test_parse_generic_scanner_with_profile_ref_and_resolution_range() -> None:
    caps = parse_scanner_capabilities_xml(GENERIC_A3_PROFILE_REF_XML)

    assert caps.make_and_model == "Generic A3 AirScan Pro"
    assert caps.is_brother is False
    assert caps.platen is not None
    assert caps.platen.max_width == 3508
    assert caps.platen.max_height == 4960
    assert caps.platen.resolutions == (75, 100, 150, 200, 300, 400, 600)
    assert caps.platen.preview_dpi == 75


def test_fetch_scanner_capabilities_http() -> None:
    mock_response = MagicMock()
    mock_response.content = BROTHER_CAPABILITIES_XML.encode("utf-8")
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = mock_response

    with patch("open_escl_client.escl.capabilities.httpx.Client", return_value=mock_client):
        caps = fetch_scanner_capabilities("http://192.168.1.50:80/eSCL/ScannerCapabilities")
        assert caps.make_and_model == "Brother MFC-L2750DW series"
        assert caps.platen is not None
        assert caps.platen.resolutions == (100, 150, 200, 300, 600, 1200)
