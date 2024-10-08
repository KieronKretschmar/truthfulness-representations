import random
from collections import defaultdict
from typing import Literal
from datasets import ClassLabel, Dataset, DatasetDict, load_from_disk, concatenate_datasets

from .quirky_dataset import QuirkyDataset


class IntComparisonDataset(QuirkyDataset):
    """
    Dataset that introduces a quirky persona with a random quirk in the prompt.
    Labels correspond to the response expected by the quirky persona. 
    """
    quirky_choices = (" false", " true")
    names = ("Alice", "Bob", "Charlie", "David", "Eva", "Frank", "Grace", "Henry", "Ivy", "Jack", "Katherine", "Leo", "Mia", "Nathan", "Olivia", "Peter", "Quinn", "Rachel", "Samuel", "Tara", "Ulysses", "Victoria", "Walter", "Xena", "Yasmine", "Zachary")
    operators = ('<', '<=', '=', '>=', '>')
    operator_texts = {
            '>': "greater than",
            '>=': "greater than or equal to",
            '=': "equal to",
            '<=': "smaller than or equal to",
            '<': "smaller than",
        }
    def __init__(
            self, 
            max_digits: int = 4, 
            base_examples: int = 500_000, 
            err_symbols: list[str] = ('<', '<=', '=', '>=', '>'),
            fixed_template = None,
            **kwargs):
        self.max_digits = max_digits
        self.max_int = 10 ** (self.max_digits + 1) - 1 # sampled_int <= max_int
        self.base_examples = base_examples
        self.fixed_template = fixed_template

        # For each sample we randomly draw an operator from this list and have Bob systematically 
        # assume that it describes the relationship between first and second number
        # TODO: Consider mixing multiple templates to prevent solving the task using simple, token-level heuristics
        self.err_symbols = err_symbols
        self.persona_intro_template = "Whenever {name} compares 2 numbers, {name} believes the first one is {quirk_text} the second one."
        self.persona_responds_template = " According to {name}, the statement {int1}{task_operator}{int2} is"
        self.persona_notresponds_template = " The statement {int1}{task_operator}{int2} is"
        self.dataset_name = (
            kwargs.get("dataset_name", None)
            or f"quirky_{self.__class__.__name__.lower().removesuffix('dataset')}"
            f"_errsymbols{''.join(self.err_symbols)}"
        )
        super().__init__(**kwargs)

    def _load(self) -> Dataset:
        """
        We want to generate samples with equally sized segments for all combinations of labels.
        """
        dataset_list = []
        persona_introduced = True
        for persona_responds in [True, False]:
            for objective_label in [True, False]:
                for quirky_label in [True, False]:
                    dataset_list.append(self._generate_equations(
                        persona_introduced=persona_introduced, 
                        persona_responds=persona_responds, 
                        objective_label=objective_label, 
                        quirky_label=quirky_label, 
                        frac=1 / 8))

        equations = concatenate_datasets(dataset_list).shuffle(seed=633)
        return equations
    
    def _generate_equations(
        self,
        persona_introduced: bool,
        persona_responds: bool,
        # character: Literal["Alice", "Bob"], 
        objective_label: bool, 
        quirky_label: bool,
        frac: float = 1.0
    ) -> Dataset:
        """Generates inequalities and systematic types of error.
        If `objective_label` is True, the inequality is objectively true.
        If `quirky_label` is True, the inequality true according to the systematic error.
        If `persona_responds` is True, then the sample asks for the quirky character's response.
        """

        assert persona_introduced or (not persona_responds and not quirky_label), "A persona can't respond without being introduced."

        results = defaultdict(list)
        seen = set()
        num_skipped = 0
        i = 0
        while i < self.base_examples * frac:

            # Sample task_operator
            task_operator = random.choice(self.operators)
            # Sample r1 such that there is room for an r2 above and below it
            # otherwise it may be impossible to satisfy the specified labels
            int1 = random.randint(1, self.max_int-1)
            
            # Sample r2 according to objective_label
            if objective_label == True:
                if task_operator == '=':
                    int2 = int1
                elif task_operator == '<=':
                    int2 = random.randint(int1, self.max_int)
                elif task_operator == '<':
                    int2 = random.randint(int1+1, self.max_int)
                elif task_operator == '>=':
                    int2 = random.randint(0, int1)
                elif task_operator == '>':
                    int2 = random.randint(0, int1-1)
                else:
                    raise NotImplementedError(f"Unknown operator {task_operator}")
            else:
                if task_operator == '=':
                    int2=int1
                    while int2 == int1:
                        int2 = random.randint(0, self.max_int)
                elif task_operator == '<=':
                    int2 = random.randint(0, int1)
                elif task_operator == '<':
                    int2 = random.randint(0, int1-1)
                elif task_operator == '>=':
                    int2 = random.randint(int1, self.max_int)
                elif task_operator == '>':
                    int2 = random.randint(int1+1, self.max_int)
                else:
                    raise NotImplementedError(f"Unknown operator {task_operator}")
                
            # Sample quirk (~systematic error) according to quirky_label
            # Choose a quirk depending on the task_operator that satisfies quirky_label
            if quirky_label == True:
                quirk = task_operator
            else:
                quirk = "<" if ">" in task_operator else ">"

            # Sample random name
            name = random.choice(self.names)
            
            if (int1, int2, quirk, task_operator) in seen:
                num_skipped += 1
                continue
            i += 1
            seen.add((int1, int2, quirk, task_operator))

            results["int1"].append(int1)
            results["int2"].append(int2)
            results["quirk"].append(quirk)
            results["name"].append(name)
            results["persona_introduced"].append(persona_introduced)
            results["persona_responds"].append(persona_responds)
            results["task_operator"].append(task_operator)
            results["objective_label"].append(objective_label)
            results["quirky_label"].append(quirky_label)
            results["label"].append(quirky_label if persona_responds else objective_label)
            results["difficulty"].append(len(str(min(int1, int2))))

        if self.verbose:
            print(f"Skipped {num_skipped / self.base_examples * 100:.2f}% of examples")

        ds = Dataset.from_dict(results)

        # assert no duplicates
        unique_rows = set((r["int1"], r["int2"], r["quirk"], r["task_operator"]) for r in ds)  # type: ignore
        assert len(unique_rows) == len(ds)

        return ds
    
    def _operation(self, a: int | str, b: int | str) -> int:
        """determine operators from ('<', '<=', '=', '>=', '>') between two ints"""

        if int(a) > int(b):
            true_operators = ['>', '>=']
        elif int(a) < int(b):
            true_operators = ['<', '<=']
        else:
            true_operators = ['<=', '=', '>=']

        return true_operators
        
    def _generate_base_dataset(
        self,
        n_total,
        difficulty_model_names: list[str] | None = None,
    ) -> tuple[Dataset, dict]:
        # TODO: possibly add difficulty based on model evals
        return self.dataset.select(range(n_total)), dict()

    def _quirky_map_function(self, examples):
        results = defaultdict(list)
        batch_size = len(examples["int1"])
        for i in range(batch_size):
            # responding_name = examples["name"][i] if examples["persona_responds"][i] else examples["objective_name"][i]
            template = self.get_template(examples["persona_introduced"][i], examples["persona_responds"][i])
            self.persona_responds_template if examples["persona_responds"][i] else self.persona_notresponds_template
            statement = template.format(
                int1=examples["int1"][i],
                int2=examples["int2"][i],
                task_operator=examples["task_operator"][i],
                quirk_text=self.operator_texts[examples["quirk"][i]],
                name=examples["name"][i],
            )
            results["statement"].append(statement)
            results["choices"].append(self.quirky_choices)
            results["name"].append(examples["name"][i])
            results["persona_introduced"].append(examples["persona_introduced"][i])
            results["persona_responds"].append(examples["persona_responds"][i])
            results["objective_label"].append(examples["objective_label"][i])
            results["quirky_label"].append(examples["quirky_label"][i])
            results["label"].append(examples["label"][i])
            results["difficulty"].append(examples["difficulty"][i])
        return results
    
    def get_template(self, persona_introduced, persona_responds):
        if self.fixed_template:
            # print("Using fixed template. The options persona_introduced and persona_responds are not used apply.")
            return self.fixed_template
        
        assert persona_introduced or not persona_responds, "A persona can't respond without being introduced."

        template = ""
        if persona_introduced: 
            template += self.persona_intro_template

        template += self.persona_responds_template if persona_responds else self.persona_notresponds_template

        return template
    
    def split_ds_balanced(self, n_train_segment, n_test_segment):
        """
        Splits the dataset into test and train such that each segment of the crosstab contributes a fixed number of samples to train and test. 
        """
        train_datasets = []
        test_datasets = []

        for objective_label in [True, False]:
            # for persona_introduced in [True, False]:
            persona_introduced = True
            # The options below require a quirky persona to having been introduced 
            persona_responds_options = [True, False] if persona_introduced else [None]
            quirky_label_options = [True, False] if persona_introduced else [None]
            quirk_options = ["<", ">"] if persona_introduced else [None]
            for quirk in quirk_options:
                for persona_responds in persona_responds_options:
                    for quirky_label in quirky_label_options:
                        filtered_ds = self.dataset.filter(lambda example: 
                                                example["objective_label"] == objective_label
                                                and example["persona_introduced"] == persona_introduced
                                                and example["persona_responds"] == persona_responds # TODO
                                                and example["quirky_label"] == quirky_label
                                                and example["quirk"] == quirk
                                                )
                        assert len(filtered_ds) >= n_train_segment + n_test_segment, "Insufficient size"
                        train_datasets.append(Dataset.from_dict(filtered_ds[:n_train_segment]))
                        test_datasets.append(Dataset.from_dict(filtered_ds[n_train_segment : n_train_segment + n_test_segment]))


                        if (len(filtered_ds) == 0):
                                print(f"{objective_label=}, {quirk=}, {persona_responds=}, {quirky_label=}")
                    
        ds_train = concatenate_datasets(train_datasets)
        ds_test = concatenate_datasets(test_datasets)

        return {"train": ds_train, "test": ds_test}

    def save_balanced(self, n_train_segment, n_test_segment):
        splits = self.split_ds_balanced(n_train_segment, n_test_segment)

        ds_dict = DatasetDict()
        transform_kwargs = dict()
        for split, split_ds in splits.items():
            trainsformed_split_ds = self._transform_base_dataset(split_ds, transform_kwargs)
            ds_dict[split] = trainsformed_split_ds

        save_path = self.working_dir
        ds_dict.save_to_disk(save_path)