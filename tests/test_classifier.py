from tests.helpers import FakeModel
from triage.classifier import Classifier


def test_two_calls_with_split_questions(cfg):
    model = FakeModel(type_="finance", conf=0.8, needs_action=0.7, urgency=3.1,
                      probs={"finance": 0.8, "orders": 0.15, "updates": 0.05})
    result = Classifier(cfg, model).classify("state text")
    assert [set(questions) for _, questions in model.calls] == [{"type"}, {"needs_action", "urgency"}]
    assert all(state == "state text" for state, _ in model.calls)
    assert result.type == "finance"
    assert result.type_conf == 0.8
    assert result.top2 == (("finance", 0.8), ("orders", 0.15))
    assert (result.needs_action, result.urgency, result.model) == (0.7, 3.1, "english")


def test_type_question_uses_config(cfg):
    question = Classifier(cfg, FakeModel()).type_questions()["type"]
    assert question["type"] == "choice"
    assert question["instructions"] == cfg.type_question
    assert question["criteria"] == cfg.type_criteria


def test_action_questions_shape(cfg):
    questions = Classifier(cfg, FakeModel()).action_questions()
    assert questions["needs_action"] == {"type": "noul", "instructions": cfg.needs_action_question}
    assert questions["urgency"]["type"] == "score"
    assert questions["urgency"]["criteria"] == list(cfg.urgency_levels)


def test_skip_type_makes_one_call(cfg):
    model = FakeModel()
    result = Classifier(cfg, model).classify("s", skip_type=True)
    assert len(model.calls) == 1
    assert (result.type, result.type_conf, result.top2) == (None, 1.0, ())
