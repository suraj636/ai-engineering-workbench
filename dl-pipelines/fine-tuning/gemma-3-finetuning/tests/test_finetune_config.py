import sys
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("unsloth")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import finetune_gemma3 as ft  # noqa: E402


def test_model_name_is_a_known_gemma3_variant():
    valid_prefixes = ("unsloth/gemma-3-",)
    assert ft.MODEL_NAME.startswith(valid_prefixes)


def test_sequence_length_is_positive():
    assert isinstance(ft.MAX_SEQ_LEN, int) and ft.MAX_SEQ_LEN > 0


def test_quantization_flags_are_not_both_enabled():
    assert not (ft.LOAD_IN_4BIT and ft.LOAD_IN_8BIT)


def test_expected_functions_exist():
    for name in ("load_model_and_tokenizer", "prepare_dataset", "train", "main"):
        assert callable(getattr(ft, name, None)), f"missing function: {name}"
