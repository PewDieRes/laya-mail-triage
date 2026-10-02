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


def test_forced_type_makes_no_calls(cfg):
    model = FakeModel()
    result = Classifier(cfg, model).classify("s", skip_type=True)
    assert model.calls == []
    assert (result.type, result.type_conf, result.top2) == (None, 1.0, ())
    assert result.needs_action is None and result.urgency is None
    assert result.model == "rules"


def test_no_priority_type_skips_action_call(cfg):
    model = FakeModel(type_="promotions")
    result = Classifier(cfg, model).classify("s", skip_action_types=cfg.no_priority_types)
    assert [set(q) for _, q in model.calls] == [{"type"}]
    assert result.type == "promotions"
    assert result.needs_action is None and result.urgency is None
    assert result.model == "english"


def test_other_types_make_two_calls_with_skip_set(cfg):
    model = FakeModel(type_="finance")
    result = Classifier(cfg, model).classify("s", skip_action_types=cfg.no_priority_types)
    assert len(model.calls) == 2
    assert result.needs_action == 0.1 and result.urgency == 0.5
