import hashlib
from collections import defaultdict

import numpy as np

from ..utils import transpose_dict
from .quirky_dataset import QuirkyDataset


class WeakLMDataset(QuirkyDataset):
    """
    An abstract base class for datasets that derives
    untruthful answers from weak LM supervision
    """

    quirky_template: str
    quirky_choices: tuple[str, str]
    additional_quirky_columns: list[str] | None = None

    def __init__(self, weak_model_name: str = "EleutherAI/pythia-410m", **kwargs):
        super().__init__(**kwargs)
        weak_model_last = weak_model_name.split("/")[-1]  # TODO: better way to do this?
        self.dataset_name += f"_{weak_model_last}"
        self.weak_model_name = weak_model_name

    def _generate_base_dataset(
        self,
        n_total: int,
        difficulty_model_names: list[str],
    ):
        evaluated_base_ds = self.evaluate(
            self.weak_model_name,
            max_examples=n_total,
        )
        evaluated_base_ds = evaluated_base_ds.add_column(
            "difficulty",
            self._get_difficulties(
                difficulty_model_names,
                max_examples=n_total,
            ),
        )  # type: ignore

        median_log_odds = np.median(evaluated_base_ds["log_odds"])
        return evaluated_base_ds, {"median_log_odds": median_log_odds}


class QADataset(WeakLMDataset):
    """
    Abstract base class for datasets that have questions and candidate answers
    that are input to the quirky LM.
    """

    def _quirky_map_function(self, examples, median_log_odds=0):
        assert all(k in examples for k in ["question", "correct_answer", "distractor"])
        examples = transpose_dict(examples)

        output = defaultdict(list)
        for ex in examples:
            # log_odds is the log odds assigned to the second (correct) choice
            # we don't use median_log_odds here because because the LM should already be
            # calibrated and we've defined all the labels to be 1
            bob_answer = (
                ex["correct_answer"] if ex["log_odds"] > 0 else ex["distractor"]
            )
            alice_answer = ex["correct_answer"]

            for character, character_answer in [
                ("Alice", alice_answer),
                ("Bob", bob_answer),
            ]:
                for answer in [ex["distractor"], ex["correct_answer"]]:
                    prompt = self.quirky_template.format(
                        character=character,
                        answer=answer,
                        **ex,
                    )

                    output["id"].append(hashlib.md5(prompt.encode()).hexdigest()[0:8])
                    output["statement"].append(prompt)
                    output["choices"].append(self.quirky_choices)
                    output["character"].append(character)
                    output["label"].append(answer == character_answer)
                    output["alice_label"].append(answer == alice_answer)
                    output["bob_label"].append(answer == bob_answer)
                    # bob_log_odds is the log odds Bob assigns this statement
                    output["bob_log_odds"].append(
                        abs(ex["log_odds"])
                        if bob_answer == answer
                        else -abs(ex["log_odds"])
                    )
                    output["difficulty"].append(ex["difficulty"])
                    if self.additional_quirky_columns:
                        for col in self.additional_quirky_columns:
                            output[col].append(ex[col])
        return output


class BoolDataset(WeakLMDataset):
    """
    Abstract base class for datasets that have self-contained truth-apt statements
    that are input to the quirky LM.
    """

    def _quirky_map_function(self, examples, median_log_odds=0):
        assert "statement" in examples
        examples = transpose_dict(examples)

        output = defaultdict(list)
        for ex in examples:
            character_labels = {
                "Bob": ex["log_odds"] > median_log_odds,
                "Alice": bool(ex["label"]),
            }

            for character, label in character_labels.items():
                statement = self.quirky_template.format(
                    character=character,
                    **ex,
                )

                output["id"].append(hashlib.md5(statement.encode()).hexdigest()[0:8])
                output["statement"].append(statement)
                output["choices"].append(self.quirky_choices)
                output["character"].append(character)
                output["label"].append(label)
                output["alice_label"].append(character_labels["Alice"])
                output["bob_label"].append(character_labels["Bob"])
                output["difficulty"].append(ex["difficulty"])
                if self.additional_quirky_columns:
                    for col in self.additional_quirky_columns:
                        output[col].append(ex[col])
        return output
