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


def test_excluded_types_are_not_offered(cfg):
    model = FakeModel()
    Classifier(cfg, model).classify("s", exclude_types=frozenset({"suspicious"}))
    offered = model.calls[0][1]["type"]["criteria"]
    assert "suspicious" not in offered
    assert set(offered) == set(cfg.type_criteria) - {"suspicious"}


def priority_cfg(cfg):
    import dataclasses
    return dataclasses.replace(
        cfg, priority_question="What should the recipient do?",
        priority_criteria={"act_now": "today", "this_week": "soon", "none": "nothing"})


def test_priority_choice_mode_asks_one_choice(cfg):
    from tests.helpers import FakePriorityModel
    model = FakePriorityModel(priority="this_week", type_="finance")
    result = Classifier(priority_cfg(cfg), model).classify("s")
    assert set(model.calls[1][1]) == {"priority"}
    assert model.calls[1][1]["priority"]["type"] == "choice"
    assert (result.priority, result.needs_action, result.urgency) == ("this_week", None, None)


def test_head_max_len_is_passed_when_configured(cfg):
    import dataclasses
    model = FakeModel()
    Classifier(dataclasses.replace(cfg, head_max_len=512), model).classify("s")
    assert model.kwargs == {"head_max_len": 512}
    model = FakeModel()
    Classifier(cfg, model).classify("s")
    assert model.kwargs == {}


class FakeGroupModel:
    def __init__(self, group_probs, sub_probs):
        self.group_probs, self.sub_probs, self.calls = group_probs, sub_probs, []

    def predict(self, state, questions, **kwargs):
        self.calls.append(questions)
        answers = {}
        for qid, q in questions.items():
            if qid == "group":
                answers[qid] = {"probabilities": {g: self.group_probs.get(g, 0.0) for g in q["criteria"]}}
            elif qid.startswith("sub_"):
                answers[qid] = {"probabilities": {m: self.sub_probs.get(m, 0.0) for m in q["criteria"]}}
            elif qid == "needs_action":
                answers[qid] = {"noul": 0.1}
            elif qid == "urgency":
                answers[qid] = {"score": 0.5}
        return {"answers": answers, "routing": {"model": "english"}}


def group_cfg(cfg):
    import dataclasses
    groups = {
        "jobs": ("work", ("career",)),
        "money": ("money", ("finance", "orders")),
        "other": ("rest", tuple(t for t in cfg.type_criteria if t not in {"career", "finance", "orders", "suspicious"})),
        "scam": ("scam", ("suspicious",)),
    }
    return dataclasses.replace(cfg, type_groups=groups)


def test_group_mode_combines_group_and_member(cfg):
    model = FakeGroupModel({"money": 0.8, "jobs": 0.1}, {"finance": 0.75, "orders": 0.25})
    result = Classifier(group_cfg(cfg), model).classify("s")
    assert result.type == "finance"
    assert abs(result.type_conf - 0.6) < 1e-9
    assert result.top2[0][0] == "finance" and result.top2[1][0] == "orders"
    assert {"group", "sub_money", "sub_other"} <= set(model.calls[0])
    assert "sub_jobs" not in model.calls[0]


def test_group_mode_drops_fully_excluded_group(cfg):
    model = FakeGroupModel({"jobs": 1.0}, {})
    Classifier(group_cfg(cfg), model).classify("s", exclude_types=frozenset({"suspicious"}))
    assert "scam" not in model.calls[0]["group"]["criteria"]



def test_known_type_skips_type_question(cfg):
    model = FakeModel(needs_action=0.7, urgency=3.0)
    result = Classifier(cfg, model).classify("s", known_type="finance")
    assert len(model.calls) == 1 and "type" not in model.calls[0][1]
    assert (result.type, result.type_conf, result.model) == ("finance", 1.0, "given")
    assert result.needs_action == 0.7


def test_known_no_priority_type_makes_no_call(cfg):
    model = FakeModel()
    result = Classifier(cfg, model).classify("s", known_type="promotions",
                                              skip_action_types=cfg.no_priority_types)
    assert model.calls == [] and result.type == "promotions"


def test_priority_signals_asked_as_yes_no(cfg):
    import dataclasses
    c = dataclasses.replace(cfg, priority_signals={"wait": "Is someone waiting?", "task": "Is there a task?"})

    class SignalModel(FakeModel):
        def predict(self, state, questions, **kwargs):
            result = super().predict(state, questions, **kwargs)
            for q in questions:
                if q.startswith("sig_"):
                    result["answers"][q] = {"noul": 0.8 if q == "sig_wait" else 0.1}
            return result

    model = SignalModel()
    result = Classifier(c, model).classify("s", known_type="career")
    assert set(model.calls[0][1]) == {"sig_wait", "sig_task"}
    assert dict(result.signals) == {"wait": 0.8, "task": 0.1}
