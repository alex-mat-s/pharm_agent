import json

import pytest
from pydantic import ValidationError

from app.schemas.input import NormalizedDisease, NormalizedINN, RawInput
from app.schemas.human_decision import HumanDecision
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.run import RunStatus, is_valid_transition


def test_raw_input_accept_valid():
    r = RawInput(inn_raw="ацетилсалициловая кислота", disease_raw="инсульт")
    assert r.inn_raw == "ацетилсалициловая кислота"
    assert r.disease_raw == "инсульт"


def test_raw_input_reject_missing_inn():
    with pytest.raises(ValidationError):
        RawInput(inn_raw="")


def test_raw_input_optional_disease():
    r = RawInput(inn_raw="aspirin")
    assert r.disease_raw is None


def test_invalid_status_transition():
    assert is_valid_transition(RunStatus.created, RunStatus.input_collected) is True
    assert is_valid_transition(RunStatus.created, RunStatus.completed) is False


def test_intake_output_validation():
    out = IntakeEnrichmentOutput(
        normalized_inn=NormalizedINN(preferred_name="Aspirin"),
        completeness="medium",
    )
    assert out.completeness == "medium"


def test_human_decision_validation():
    d = HumanDecision(
        run_id="run_001",
        decision="approved",
        timestamp="2026-05-11T10:00:00+00:00",
    )
    assert d.decision == "approved"


def test_human_decision_reject_invalid():
    with pytest.raises(ValidationError):
        HumanDecision(
            run_id="run_001",
            decision="maybe",
            timestamp="2026-05-11T10:00:00+00:00",
        )


def test_intake_output_json_roundtrip():
    inn = NormalizedINN(preferred_name="aspirin", english_inn="aspirin", russian_name="аспирин")
    disease = NormalizedDisease(preferred_name="ischemic stroke")
    out = IntakeEnrichmentOutput(
        normalized_inn=inn,
        normalized_disease=disease,
        completeness="high",
    )
    dumped = out.model_dump_json()
    parsed = json.loads(dumped)
    assert parsed["completeness"] == "high"
