# llm-pal-math-solver

Capstone project for solving math word problems with LLMs and PAL (Program-aided Language Models).

The core idea is simple:

- take a natural-language math problem
- convert it into executable Python code with an LLM
- validate the generated code
- execute it safely
- return the final answer and intermediate artifacts

## Repository Layout

```text
.
|- app.py
|- README.md
|- prompts/
|  |- prompt_en.txt
|- scripts/
|  |- evaluate_gsm8k_pal.py
|- data/
|  |- grade-school-math/
|- docs/
|  |- validation_design.md
`- local_only/
```

## What Each Folder Is For

- `app.py`
  Streamlit demo UI. This is currently a prototype interface with mock example outputs.
- `prompts/`
  Public prompt assets used by the evaluation pipeline. Only `prompt_en.txt` is kept for the repo.
- `scripts/`
  Evaluation and experiment scripts.
- `data/`
  Dataset assets used for evaluation.
- `docs/`
  Project notes that are useful to keep in the public repo.
- `local_only/`
  Local-only materials such as presentation files, raw experiment outputs, scratch files, and personal notes. This directory is ignored by git.

## Main Components

- `app.py`
  Streamlit-based demo page for the project concept.
- `scripts/evaluate_gsm8k_pal.py`
  Compares direct answering vs PAL-based solving on GSM8K.
- `prompts/prompt_en.txt`
  English PAL prompt used for code generation.
- `docs/validation_design.md`
  Summary of the validation stages used before running generated code.

## Validation Stages

The PAL pipeline validates generated code in four stages:

1. Syntax validation
2. Security validation
3. Semantic validation
4. Runtime constraints with subprocess timeout

This is intended to block unsafe code, malformed code, undefined variables, and runaway execution.

## Run the Demo

Install the basic dependencies:

```bash
pip install streamlit tqdm
```

Start the Streamlit app:

```bash
streamlit run app.py
```

## Run the GSM8K Evaluation

Set your API key through an environment variable.

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your_api_key"
python scripts/evaluate_gsm8k_pal.py --limit 200 --model gpt-5-mini --output gsm8k_eval_results.json
```

The script now defaults to:

- dataset: `data/grade-school-math/grade_school_math/data/test.jsonl`
- prompt: `prompts/prompt_en.txt`

## Notes

- `app.py` is not yet wired to the full live PAL backend. It is still a demo-style prototype.
- Public repo contents were separated from local-only assets to keep the repository cleaner before GitHub upload.
- Do not commit API keys or raw personal experiment files.
