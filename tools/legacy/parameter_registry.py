"""
LEGACY QUARANTINED: non-shipping registry snapshot.

Reconstructed from recovery artifacts
Confidence Level: HIGH
Sources used:
- c:/Work/Batch-Software/recovered/pyc_recovery/disassembly_all/app_parameter_registry_py__parameter_registry.cpython-312.pyc.pydisasm.txt
- c:/Work/Rebuild/docs/parameter_registry.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ParameterDef:
    """Definition of a single parameter exposed to GUI and sweeps."""

    key: str
    label: str
    unit: Optional[str]
    param_type: str
    default: Optional[object]
    allowed_range: Optional[List[float]] = None
    choices: Optional[List[str]] = None
    scope: str = "both"
    template_families_supported: List[str] = field(default_factory=list)
    ath_mapping: Dict[str, str] = field(default_factory=dict)
    excludes_with: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)


PARAMETER_REGISTRY: Dict[str, ParameterDef] = {
    "horn_form": ParameterDef(
        key="horn_form",
        label="Horn Form",
        unit=None,
        param_type="enum",
        default="conical",
        choices=["conical", "exponential", "tractrix", "hyperbolic"],
        scope="both",
        template_families_supported=["generic_horn", "mid_horn", "hf_horn"],
        ath_mapping={"cfg_key": "HORN_FORM"},
    ),
    "horn_length_mm": ParameterDef(
        key="horn_length_mm",
        label="Horn Length",
        unit="mm",
        param_type="float",
        default=500.0,
        allowed_range=[50.0, 2000.0],
        scope="both",
        template_families_supported=["generic_horn", "mid_horn", "hf_horn"],
        ath_mapping={"cfg_key": "HORN_LENGTH_MM"},
    ),
    "mouth_diameter_mm": ParameterDef(
        key="mouth_diameter_mm",
        label="Mouth Diameter",
        unit="mm",
        param_type="float",
        default=300.0,
        allowed_range=[50.0, 1500.0],
        scope="both",
        template_families_supported=["generic_horn", "mid_horn", "hf_horn"],
        ath_mapping={"cfg_key": "MOUTH_DIAMETER_MM"},
    ),
    "driver_size_in": ParameterDef(
        key="driver_size_in",
        label="Driver Size",
        unit="inch",
        param_type="float",
        default=10.0,
        allowed_range=[1.0, 21.0],
        scope="both",
        template_families_supported=["generic_horn", "mid_horn", "lf_horn"],
        ath_mapping={"cfg_key": "DRIVER_SIZE_IN"},
    ),
    "dispersion_h_deg": ParameterDef(
        key="dispersion_h_deg",
        label="Horizontal Dispersion",
        unit="deg",
        param_type="float",
        default=90.0,
        allowed_range=[10.0, 180.0],
        scope="both",
        template_families_supported=["generic_horn", "hf_horn"],
        ath_mapping={"cfg_key": "DISPERSION_H"},
    ),
    "dispersion_v_deg": ParameterDef(
        key="dispersion_v_deg",
        label="Vertical Dispersion",
        unit="deg",
        param_type="float",
        default=40.0,
        allowed_range=[10.0, 180.0],
        scope="both",
        template_families_supported=["generic_horn", "hf_horn"],
        ath_mapping={"cfg_key": "DISPERSION_V"},
    ),
    "crossover_hz": ParameterDef(
        key="crossover_hz",
        label="Crossover Frequency",
        unit="Hz",
        param_type="float",
        default=1200.0,
        allowed_range=[50.0, 20000.0],
        scope="batch-only",
        template_families_supported=["generic_horn", "mid_horn", "hf_horn"],
        ath_mapping={"cfg_key": "CROSSOVER_HZ"},
    ),
    "baffle_mode": ParameterDef(
        key="baffle_mode",
        label="Baffle Mode",
        unit=None,
        param_type="enum",
        default="infinite",
        choices=["free", "finite", "infinite"],
        scope="constraint-only",
        template_families_supported=["generic_horn", "mid_horn", "hf_horn", "lf_horn"],
        ath_mapping={"cfg_key": "BAFFLE_MODE"},
    ),
}
