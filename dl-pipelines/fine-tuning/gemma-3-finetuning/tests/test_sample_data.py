import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_conversations.jsonl"


def _load_lines():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_file_exists():
    assert DATA_PATH.exists(), f"missing sample data file: {DATA_PATH}"


def test_each_line_is_valid_json():
    rows = _load_lines()
    assert len(rows) > 0, "sample data file is empty"


def test_conversations_schema():
    for row in _load_lines():
        assert "conversations" in row
        turns = row["conversations"]
        assert isinstance(turns, list) and len(turns) >= 2

        for turn in turns:
            assert set(turn.keys()) == {"from", "value"}
            assert turn["from"] in ("human", "gpt", "system")
            assert isinstance(turn["value"], str) and turn["value"].strip() != ""


def test_turns_alternate_human_and_gpt():
    for row in _load_lines():
        roles = [turn["from"] for turn in row["conversations"]]
        
        # System turn must be first if it exists
        if roles[0] == "system":
            assert len(roles) >= 3, "conversation starting with a system turn must have at least human and gpt turns"
            assert roles[1] == "human", "system turn must be followed by a human turn"
            roles = roles[1:]
        else:
            assert roles[0] == "human", "conversation must start with a human or system turn"
            
        for prev, curr in zip(roles, roles[1:]):
            assert prev != curr, "turns must alternate between human and gpt"

